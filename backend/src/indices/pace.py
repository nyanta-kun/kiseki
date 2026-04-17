"""展開指数算出Agent

馬の過去成績から脚質（逃げ・先行・差し・追込）を判定し、
当該レースの展開予測（ペース）との適合度をスコア化する。

算出ロジック:
  1. 馬の過去 LOOKBACK_RACES 戦の passing_4（4コーナー通過順）と
     head_count（頭数）から relative_pos を計算し脚質を判定する
  2. 同レース全馬の脚質分布から展開（pace_type）を予測する
     - escape数 >= 2: ハイペース (fast)
     - escape数 == 1: 平均ペース (normal)
     - escape数 == 0: スローペース (slow)
  3. 脚質 × ペースの適合スコアテーブルで基本スコアを決定する
  4. コース特性補正（コーナーきつさ・直線長・スタート〜コーナー距離）
     - 短直線・小回り: 逃げ/先行 有利補正
     - 長直線・大回り: 差し/追い込み 有利補正
  5. 当開催前後バイアス補正（MeetBiasService）
     - 開催初期（前有利）: 逃げ/先行 に +ボーナス
     - 開催後半（後ろ有利）: 差し/追い込み に +ボーナス
  6. 出走頭数補正
     - 多頭数（≥14）+ 小回り: 前の位置取りが困難 → 外枠先行には罰則
     - 多頭数 + 短スタートコーナー距離: 逃げ馬に競り合いリスク
  7. 上がり3F平均が同条件平均より速い場合は +5 ボーナス（最大100）

制約:
  - 除外・取消（abnormality_code > 0）のレースは除外
  - passing_4 や head_count が None の場合は graceful handling
  - データなし（脚質不明）は "unknown" として中立スコア50を返す
"""

from __future__ import annotations

import logging
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RacecourseFeatures, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator
from .meet_bias import MeetBias, MeetBiasService

logger = logging.getLogger(__name__)

# 過去何戦を参照するか
LOOKBACK_RACES = 10
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0
# 上がり3F補正ボーナス
LAST_3F_BONUS = 5.0
# 最低サンプル数（上がり3F平均算出用）
MIN_LAST3F_SAMPLE = 3

# 脚質の分類閾値（relative_pos = passing_4 / head_count）
RUNNER_TYPE_THRESHOLDS = {
    "escape": (0.0, 0.25),
    "leader": (0.25, 0.45),
    "mid": (0.45, 0.65),
    "closer": (0.65, 1.0),
}

# 脚質 × ペースの適合スコアテーブル
PACE_SCORE_TABLE: dict[str, dict[str, float]] = {
    "escape": {"fast": 45.0, "normal": 70.0, "slow": 85.0},
    "leader": {"fast": 55.0, "normal": 70.0, "slow": 75.0},
    "mid": {"fast": 70.0, "normal": 65.0, "slow": 60.0},
    "closer": {"fast": 80.0, "normal": 60.0, "slow": 45.0},
    "unknown": {"fast": 50.0, "normal": 50.0, "slow": 50.0},
}

# コース特性補正の閾値
LONG_STRAIGHT_M = 450  # これ以上: 差し/追い込み有利コース
SHORT_STRAIGHT_M = 310  # これ以下: 逃げ/先行有利コース
TIGHT_CORNER = 0.65  # これ以上: 小回り（前有利）
LARGE_FIELD = 14  # これ以上: 多頭数
SHORT_START_CORNER = 120  # スタート〜コーナーがこれ以下: 前争い激化

# コース特性・当開催バイアスの最大補正幅（ポイント）
COURSE_ADJ_MAX = 6.0
MEET_ADJ_MAX = 7.0
FIELD_ADJ_MAX = 4.0

# 前走ハイペース×先行リバウンドボーナス（v18）
# バックテスト（v17 16,198R 2023-2026）: ROI差 +24.5%
PACE_REBOUND_BONUS = 6.0
# 先行判定: passing_1 / head_count がこれ以下
FRONT_POSITION_RATIO = 0.25


def _classify_runner_type(avg_relative_pos: float) -> str:
    """平均relative_posから脚質を返す。

    Args:
        avg_relative_pos: 平均 passing_4 / head_count の値（0-1）

    Returns:
        脚質文字列: "escape" / "leader" / "mid" / "closer"
    """
    for runner_type, (low, high) in RUNNER_TYPE_THRESHOLDS.items():
        if low <= avg_relative_pos < high:
            return runner_type
    # 1.0丁度（最後方）は closer に含める
    return "closer"


class PaceIndexCalculator(IndexCalculator):
    """展開指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        super().__init__(db)
        # 上がり3F平均のキャッシュ（コース・距離・馬場ごと）
        self._last3f_avg_cache: dict[tuple[str, int, str], float | None] = {}
        self._meet_bias = MeetBiasService(db)
        # コース特徴キャッシュ（セッション非依存の SimpleNamespace で保持）
        self._course_features: dict[str, SimpleNamespace] | None = None
        # 距離別 first_3f 中央値キャッシュ（前走ハイペース判定用）
        self._first3f_medians: dict[int, float] | None = None

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の展開指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            展開指数（0-100）。データ不足時は 50.0。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        # 単一馬の脚質を判定
        past_rows = await self._get_past_results_for_horse(horse_id, race.date, race_id)
        runner_type = self._determine_runner_type(past_rows)

        # 全馬の脚質を集計してペースを予測
        all_entries_result = await self.db.execute(select(RaceEntry).where(RaceEntry.race_id == race_id))
        all_entries = all_entries_result.scalars().all()
        all_horse_ids = [e.horse_id for e in all_entries]
        all_rows_map = await self._get_past_results_batch(all_horse_ids, race.date, race_id)

        runner_types = {
            hid: self._determine_runner_type(rows) for hid, rows in all_rows_map.items()
        }
        # エントリがあるが過去データなし → unknown
        for hid in all_horse_ids:
            if hid not in runner_types:
                runner_types[hid] = "unknown"

        pace_type = self._predict_pace(runner_types)
        base_score = PACE_SCORE_TABLE[runner_type][pace_type]

        # 上がり3F補正
        horse_past_rows = all_rows_map.get(horse_id, [])
        score = await self._apply_last3f_bonus(base_score, horse_past_rows, race)

        return round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の展開指数を一括算出する。

        N+1 を回避するため、全馬の過去レース結果を単一クエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: pace_index} のdict。エントリが存在しない場合は空dict。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries_result = await self.db.execute(select(RaceEntry).where(RaceEntry.race_id == race_id))
        entries = entries_result.scalars().all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]

        # Step1: 全馬の過去成績を一括取得
        rows_map = await self._get_past_results_batch(horse_ids, race.date, race_id)

        # Step2: 各馬の脚質を判定
        runner_types: dict[int, str] = {}
        for hid in horse_ids:
            past_rows = rows_map.get(hid, [])
            runner_types[hid] = self._determine_runner_type(past_rows)

        # Step3: レース全体のペースを1回だけ予測
        pace_type = self._predict_pace(runner_types)

        # Step4: コース特性・当開催バイアス・頭数を一度だけ取得
        course_feat = await self._get_course_features(race.course)
        meet_bias = await self._meet_bias.get_bias(race)
        head_count = race.head_count or len(horse_ids)

        # Step5: 前走ハイペース判定に必要な距離別 first_3f 中央値を事前ロード
        await self._ensure_first3f_medians()

        # Step6: 各馬のスコア算出
        result: dict[int, float] = {}
        for hid in horse_ids:
            runner_type = runner_types[hid]
            base_score = PACE_SCORE_TABLE[runner_type][pace_type]
            horse_past_rows = rows_map.get(hid, [])
            score = await self._apply_last3f_bonus(base_score, horse_past_rows, race)
            score = self._apply_course_adjustment(score, runner_type, course_feat)
            score = self._apply_meet_bias_adjustment(score, runner_type, meet_bias)
            score = self._apply_field_size_adjustment(score, runner_type, head_count, course_feat)
            score = self._apply_pace_rebound_bonus(score, horse_past_rows)
            result[hid] = round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _get_past_results_for_horse(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> list[Any]:
        """単一馬の過去レース結果を取得する。

        Args:
            horse_id: horses.id
            before_date: この日付より前のレースのみ取得（YYYYMMDD）
            exclude_race_id: 当該レースは除外

        Returns:
            [(RaceResult, Race), ...]（日付降順, 最大 LOOKBACK_RACES 件）
        """
        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
        )
        result = await self.db.execute(stmt)
        return list(result.all())

    async def _get_past_results_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, list[Any]]:
        """複数馬の過去レース結果を単一クエリで一括取得する。

        Args:
            horse_ids: 対象 horses.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 当該レースは除外

        Returns:
            {horse_id: [(RaceResult, Race), ...]}（各馬最大 LOOKBACK_RACES 件）
        """
        if not horse_ids:
            return {}

        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        db_result = await self.db.execute(stmt)
        rows = db_result.all()

        result_map: dict[int, list[Any]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)

        for row in rows:
            hid = row.RaceResult.horse_id
            if count_map[hid] < LOOKBACK_RACES:
                result_map[hid].append(row)
                count_map[hid] += 1

        return dict(result_map)

    def _determine_runner_type(self, rows: list[Any]) -> str:
        """過去レース結果から脚質を判定する。

        passing_4 と head_count から relative_pos（0-1）を計算し、
        その平均値で脚質を分類する。

        Args:
            rows: [(RaceResult, Race), ...] 過去レース結果

        Returns:
            脚質文字列: "escape" / "leader" / "mid" / "closer" / "unknown"
        """
        relative_positions: list[float] = []

        for row in rows:
            result: RaceResult = row.RaceResult
            race: Race = row.Race

            passing_4 = result.passing_4
            head_count = race.head_count

            if passing_4 is None or head_count is None or head_count <= 0:
                continue

            rel_pos = passing_4 / head_count
            relative_positions.append(rel_pos)

        if not relative_positions:
            return "unknown"

        avg_rel_pos = sum(relative_positions) / len(relative_positions)
        return _classify_runner_type(avg_rel_pos)

    async def _ensure_first3f_medians(self) -> None:
        """距離別 first_3f 中央値をキャッシュする（セッション内1回のみ実行）。

        前走ハイペース判定に使用する。
        SELECT は PERCENTILE_CONT による1クエリで完結する。
        """
        if self._first3f_medians is not None:
            return
        from sqlalchemy import text as sa_text
        rows = (
            await self.db.execute(
                sa_text(
                    """
                    SELECT distance,
                           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CAST(first_3f AS float))
                               AS median_f3f
                    FROM keiba.races
                    WHERE first_3f IS NOT NULL
                      AND first_3f > 0
                      AND surface NOT LIKE '障%'
                    GROUP BY distance
                    """
                )
            )
        ).all()
        self._first3f_medians = {int(r.distance): float(r.median_f3f) for r in rows}

    def _apply_pace_rebound_bonus(
        self,
        score: float,
        prev_rows: list[Any],
    ) -> float:
        """前走ハイペース×先行馬のリバウンドボーナスを適用する（v18）。

        前走で「先行ポジション（passing_1 ≤ head_count × 0.25）かつ
        ハイペース（first_3f < 距離別中央値）」だった馬は
        市場に過小評価されやすく、今走巻き返しの可能性が高い。

        バックテスト（v17 16,198R 2023-2026）: ROI差 +24.5%

        Args:
            score: 現在の展開スコア
            prev_rows: 過去レース結果（新しい順、最初の要素が直前レース）

        Returns:
            補正後スコア（条件不成立時は変更なし）
        """
        if not prev_rows or self._first3f_medians is None:
            return score

        latest = prev_rows[0]
        result: RaceResult = latest.RaceResult
        race: Race = latest.Race

        passing_1 = result.passing_1
        first_3f = float(race.first_3f) if race.first_3f is not None else None
        distance = race.distance
        head_count = race.head_count

        if passing_1 is None or first_3f is None or distance is None or head_count is None:
            return score
        if head_count <= 0:
            return score

        # 先行判定: passing_1 が頭数の上位 FRONT_POSITION_RATIO 以内
        was_front = float(passing_1) / float(head_count) <= FRONT_POSITION_RATIO
        if not was_front:
            return score

        # ハイペース判定: first_3f が距離別中央値より速い（小さい）
        median_f3f = self._first3f_medians.get(int(distance))
        if median_f3f is None:
            return score

        if first_3f < median_f3f:
            score += PACE_REBOUND_BONUS

        return score

    async def _get_course_features(self, course_code: str) -> SimpleNamespace | None:
        """コース特徴をキャッシュ付きで返す。

        expunge_all() 後もデタッチされないよう ORM インスタンスを保持しない。
        """
        if self._course_features is None:
            result = await self.db.execute(select(RacecourseFeatures))
            rows = result.scalars().all()
            self._course_features = {
                r.course_code: SimpleNamespace(
                    straight_distance=r.straight_distance,
                    corner_tightness=r.corner_tightness,
                    start_to_corner_m=r.start_to_corner_m,
                )
                for r in rows
            }
        return self._course_features.get(course_code)

    def _apply_course_adjustment(
        self,
        score: float,
        runner_type: str,
        feat: SimpleNamespace | None,
    ) -> float:
        """コース特性に基づいて脚質スコアを補正する。

        長直線・大回りコース: 差し/追い込み有利（前走有利コースとは逆）
        短直線・小回りコース: 逃げ/先行有利
        """
        if feat is None:
            return score

        straight = float(feat.straight_distance)
        tightness = float(feat.corner_tightness) if feat.corner_tightness else 0.5
        _start_to_corner = int(feat.start_to_corner_m) if feat.start_to_corner_m else 200

        # 直線長による補正
        if straight >= LONG_STRAIGHT_M:
            ratio = min(1.0, (straight - LONG_STRAIGHT_M) / 200)
            if runner_type in ("closer", "mid"):
                score += ratio * COURSE_ADJ_MAX
            elif runner_type == "escape":
                score -= ratio * COURSE_ADJ_MAX * 0.5
        elif straight <= SHORT_STRAIGHT_M:
            ratio = min(1.0, (SHORT_STRAIGHT_M - straight) / 50)
            if runner_type in ("escape", "leader"):
                score += ratio * COURSE_ADJ_MAX
            elif runner_type == "closer":
                score -= ratio * COURSE_ADJ_MAX * 0.5

        # コーナーきつさによる補正（小回りは前/先行有利）
        if tightness >= TIGHT_CORNER:
            ratio = min(1.0, (tightness - TIGHT_CORNER) / 0.35)
            if runner_type in ("escape", "leader"):
                score += ratio * COURSE_ADJ_MAX * 0.5
            elif runner_type == "closer":
                score -= ratio * COURSE_ADJ_MAX * 0.3

        return score

    def _apply_meet_bias_adjustment(
        self,
        score: float,
        runner_type: str,
        bias: MeetBias,
    ) -> float:
        """当開催の前後バイアスによる補正。

        front_back > 0 (前有利の開催): 逃げ/先行に加算、差し/追い込みに減算
        front_back < 0 (後ろ有利の開催): 差し/追い込みに加算、逃げ/先行に減算
        """
        fb = bias.front_back  # -1.0 〜 +1.0
        if abs(fb) < 0.05:
            return score  # ほぼ中立

        if runner_type in ("escape", "leader"):
            score += fb * MEET_ADJ_MAX
        elif runner_type in ("mid", "closer"):
            score -= fb * MEET_ADJ_MAX

        return score

    def _apply_field_size_adjustment(
        self,
        score: float,
        runner_type: str,
        head_count: int,
        feat: SimpleNamespace | None,
    ) -> float:
        """出走頭数 × コース特性による補正。

        多頭数 + 小回り/短スタートコーナー: 前の位置取りが困難になり
        逃げ馬同士が競り合う → 逃げにとってはリスク増（ハイペースになりやすい）
        多頭数 + 長直線: 外から差せるため差し/追い込みに有利
        """
        if head_count < LARGE_FIELD or feat is None:
            return score

        tightness = float(feat.corner_tightness) if feat.corner_tightness else 0.5
        start_to_corner = int(feat.start_to_corner_m) if feat.start_to_corner_m else 200
        straight = float(feat.straight_distance)

        ratio = min(1.0, (head_count - LARGE_FIELD) / 4)  # 14頭=0, 18頭=1

        # 小回り + 多頭数: コーナー前の混雑で前の位置取りリスク
        if tightness >= TIGHT_CORNER:
            if runner_type == "escape":
                score -= ratio * FIELD_ADJ_MAX * 0.8  # 逃げは競り合いリスク
            elif runner_type == "leader":
                score -= ratio * FIELD_ADJ_MAX * 0.3

        # 短スタート〜コーナー + 多頭数: 外枠の先行馬が位置取り困難
        if start_to_corner <= SHORT_START_CORNER:
            if runner_type in ("escape", "leader"):
                score -= ratio * FIELD_ADJ_MAX * 0.5

        # 長直線 + 多頭数: 外から差せる分、差し/追い込みが有利に
        if straight >= LONG_STRAIGHT_M:
            if runner_type in ("closer", "mid"):
                score += ratio * FIELD_ADJ_MAX * 0.5

        return score

    def _predict_pace(self, runner_types: dict[int, str]) -> str:
        """全馬の脚質分布からペースを予測する。

        逃げ馬（escape）の頭数でハイペース/スローペースを判定する。

        Args:
            runner_types: {horse_id: runner_type} の辞書

        Returns:
            ペース種別: "fast" / "normal" / "slow"
        """
        escape_count = sum(1 for rt in runner_types.values() if rt == "escape")

        if escape_count >= 2:
            return "fast"
        elif escape_count == 1:
            return "normal"
        else:
            return "slow"

    async def _apply_last3f_bonus(self, base_score: float, past_rows: list[Any], race: Race) -> float:
        """上がり3F補正を適用する。

        馬の上がり3F平均が同条件（コース・距離・馬場）の平均より速い場合に
        LAST_3F_BONUS を加算する。

        Args:
            base_score: 補正前のスコア
            past_rows: 馬の過去レース結果 [(RaceResult, Race), ...]
            race: 対象レース（条件特定用）

        Returns:
            補正後スコア
        """
        if not past_rows:
            return base_score

        # 馬の上がり3F平均を計算
        last3f_values = [
            float(row.RaceResult.last_3f) for row in past_rows if row.RaceResult.last_3f is not None
        ]

        if not last3f_values:
            return base_score

        horse_avg_last3f = sum(last3f_values) / len(last3f_values)

        # 同条件の上がり3F平均を取得
        course = race.course
        distance = race.distance or 0
        surface = race.surface or ""
        cond_avg = await self._get_avg_last3f(course, distance, surface)

        if cond_avg is None:
            return base_score

        # 馬の平均が条件平均より速い（小さい）場合にボーナス
        if horse_avg_last3f < cond_avg:
            return base_score + LAST_3F_BONUS

        return base_score

    async def _get_avg_last3f(self, course: str, distance: int, surface: str) -> float | None:
        """同条件の上がり3F平均を返す。

        同セッション内でキャッシュし、DBアクセスを最小化する。

        Args:
            course: 場コード
            distance: 距離（m）
            surface: 馬場種別（芝/ダ/障）

        Returns:
            上がり3F平均（秒）。サンプル不足時は None。
        """
        cache_key = (course, distance, surface)
        if cache_key in self._last3f_avg_cache:
            return self._last3f_avg_cache[cache_key]

        stmt = (
            select(
                func.avg(RaceResult.last_3f).label("avg_last3f"),
                func.count(RaceResult.id).label("cnt"),
            )
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                Race.course == course,
                Race.distance == distance,
                Race.surface == surface,
                RaceResult.last_3f.isnot(None),
                RaceResult.abnormality_code == 0,
            )
        )
        result = await self.db.execute(stmt)
        row = result.first()

        if row is None or row.cnt is None or int(row.cnt) < MIN_LAST3F_SAMPLE:
            self._last3f_avg_cache[cache_key] = None
            return None

        avg = float(row.avg_last3f) if row.avg_last3f else None
        self._last3f_avg_cache[cache_key] = avg
        return avg
