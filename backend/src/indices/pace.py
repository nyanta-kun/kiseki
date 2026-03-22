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
  4. 上がり3F平均が同条件平均より速い場合は +5 ボーナス（最大100）

制約:
  - 除外・取消（abnormality_code > 0）のレースは除外
  - passing_4 や head_count が None の場合は graceful handling
  - データなし（脚質不明）は "unknown" として中立スコア50を返す
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator

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
    "mid":    {"fast": 70.0, "normal": 65.0, "slow": 60.0},
    "closer": {"fast": 80.0, "normal": 60.0, "slow": 45.0},
    "unknown": {"fast": 50.0, "normal": 50.0, "slow": 50.0},
}


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

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemy セッション
        """
        super().__init__(db)
        # 上がり3F平均のキャッシュ（コース・距離・馬場ごと）
        self._last3f_avg_cache: dict[tuple[str, int, str], float | None] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の展開指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            展開指数（0-100）。データ不足時は 50.0。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        # 単一馬の脚質を判定
        past_rows = self._get_past_results_for_horse(horse_id, race.date, race_id)
        runner_type = self._determine_runner_type(past_rows)

        # 全馬の脚質を集計してペースを予測
        all_entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        all_horse_ids = [e.horse_id for e in all_entries]
        all_rows_map = self._get_past_results_batch(all_horse_ids, race.date, race_id)

        runner_types = {
            hid: self._determine_runner_type(rows)
            for hid, rows in all_rows_map.items()
        }
        # エントリがあるが過去データなし → unknown
        for hid in all_horse_ids:
            if hid not in runner_types:
                runner_types[hid] = "unknown"

        pace_type = self._predict_pace(runner_types)
        base_score = PACE_SCORE_TABLE[runner_type][pace_type]

        # 上がり3F補正
        horse_past_rows = all_rows_map.get(horse_id, [])
        score = self._apply_last3f_bonus(base_score, horse_past_rows, race)

        return round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の展開指数を一括算出する。

        N+1 を回避するため、全馬の過去レース結果を単一クエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: pace_index} のdict。エントリが存在しない場合は空dict。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]

        # Step1: 全馬の過去成績を一括取得
        rows_map = self._get_past_results_batch(horse_ids, race.date, race_id)

        # Step2: 各馬の脚質を判定
        runner_types: dict[int, str] = {}
        for hid in horse_ids:
            past_rows = rows_map.get(hid, [])
            runner_types[hid] = self._determine_runner_type(past_rows)

        # Step3: レース全体のペースを1回だけ予測
        pace_type = self._predict_pace(runner_types)

        # Step4: 各馬のスコア算出
        result: dict[int, float] = {}
        for hid in horse_ids:
            runner_type = runner_types[hid]
            base_score = PACE_SCORE_TABLE[runner_type][pace_type]
            horse_past_rows = rows_map.get(hid, [])
            score = self._apply_last3f_bonus(base_score, horse_past_rows, race)
            result[hid] = round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _get_past_results_for_horse(
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
        return (
            self.db.query(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
            .all()
        )

    def _get_past_results_batch(
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

        rows = (
            self.db.query(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
            .all()
        )

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

    def _apply_last3f_bonus(
        self, base_score: float, past_rows: list[Any], race: Race
    ) -> float:
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
            float(row.RaceResult.last_3f)
            for row in past_rows
            if row.RaceResult.last_3f is not None
        ]

        if not last3f_values:
            return base_score

        horse_avg_last3f = sum(last3f_values) / len(last3f_values)

        # 同条件の上がり3F平均を取得
        course = race.course
        distance = race.distance or 0
        surface = race.surface or ""
        cond_avg = self._get_avg_last3f(course, distance, surface)

        if cond_avg is None:
            return base_score

        # 馬の平均が条件平均より速い（小さい）場合にボーナス
        if horse_avg_last3f < cond_avg:
            return base_score + LAST_3F_BONUS

        return base_score

    def _get_avg_last3f(
        self, course: str, distance: int, surface: str
    ) -> float | None:
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

        row = (
            self.db.query(
                func.avg(RaceResult.last_3f).label("avg_last3f"),
                func.count(RaceResult.id).label("cnt"),
            )
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                Race.course == course,
                Race.distance == distance,
                Race.surface == surface,
                RaceResult.last_3f.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .first()
        )

        if row is None or row.cnt is None or int(row.cnt) < MIN_LAST3F_SAMPLE:
            self._last3f_avg_cache[cache_key] = None
            return None

        avg = float(row.avg_last3f) if row.avg_last3f else None
        self._last3f_avg_cache[cache_key] = avg
        return avg
