"""展開ハンデ指数算出 Agent

レース全体の展開予測（ペース・前後バイアス・内外バイアス）と、各馬の脚質・枠番との
適合度を統合した「展開的有利度」を 0-100 で算出する。

既存の pace_index（脚質×ペース適合）と position_advantage（枠番統計）を統合し、
線形和では捉えられなかった「速度上位 × 不利展開 = 着外」のような相互作用を
1 つの強い指数として表現するための calculator。

算出フロー:
  1. 全馬の脚質判定（pace.py の RUNNER_TYPE_THRESHOLDS と同じ閾値）
  2. レース予想ペース判定（逃げ馬数 + 先行馬比率の連続スコア化）
  3. 脚質 × 予想ペースの基本適合スコア（PACE_SCORE_TABLE 流用）
  4. コース特性補正（直線長・コーナーきつさ・スタート〜コーナー距離）
  5. 枠番統計補正（同コース・同距離・同馬場の枠別勝率→偏差スコア）
  6. 当開催バイアス補正（脚質×前後バイアス、枠番×内外バイアス）
  7. 多頭数補正
  8. 前走ハイペース×先行リバウンドボーナス（v18 既存）
  9. 0-100 にクリップして返す

DB 保存は CalculatedIndex.pace_index カラムを流用（v24 以降は意味が変わる）。
position_advantage カラムは frame_bias.py 互換のため別途 FrameBiasCalculator が
担当するが、composite_index の重みは 0 となる（v24）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    JockeyRunningStyleStats,
    Race,
    RacecourseFeatures,
    RaceEntry,
    RaceResult,
)
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator
from .meet_bias import MeetBiasService

logger = logging.getLogger(__name__)

# 過去何戦を参照するか（脚質判定）
LOOKBACK_RACES = 10
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0

# 脚質の分類閾値（relative_pos = passing_4 / head_count）
RUNNER_TYPE_THRESHOLDS: dict[str, tuple[float, float]] = {
    "escape": (0.0, 0.25),
    "leader": (0.25, 0.45),
    "mid": (0.45, 0.65),
    "closer": (0.65, 1.0),
}

# 脚質 × ペースの適合スコアテーブル（pace.py 由来。展開ハンデの基本骨格）
PACE_SCORE_TABLE: dict[str, dict[str, float]] = {
    "escape": {"fast": 45.0, "normal": 70.0, "slow": 85.0},
    "leader": {"fast": 55.0, "normal": 70.0, "slow": 75.0},
    "mid":    {"fast": 70.0, "normal": 65.0, "slow": 60.0},
    "closer": {"fast": 80.0, "normal": 60.0, "slow": 45.0},
    "unknown": {"fast": 50.0, "normal": 50.0, "slow": 50.0},
}

# レース予想ペース判定の閾値（連続値→離散ラベル）
# escape_count + leader_count*0.5 を pace_indicator として使う
PACE_INDICATOR_FAST = 2.5   # これ以上 → fast（escape 2頭以上 or escape1+leader3以上）
PACE_INDICATOR_SLOW = 0.5   # これ以下 → slow

# コース特性補正の閾値
LONG_STRAIGHT_M = 450
SHORT_STRAIGHT_M = 310
TIGHT_CORNER = 0.65
LARGE_FIELD = 14
SHORT_START_CORNER = 120

# 補正幅（ポイント）
COURSE_ADJ_MAX = 6.0
MEET_ADJ_MAX = 7.0
FIELD_ADJ_MAX = 4.0
FRAME_ADJ_MAX = 5.0   # 枠番統計補正の最大ポイント

# 前走ハイペース×先行リバウンド（v18 既存）
PACE_REBOUND_BONUS = 6.0
FRONT_POSITION_RATIO = 0.25

# 上がり3F補正
LAST_3F_BONUS = 5.0
MIN_LAST3F_SAMPLE = 3

# 枠番統計
FRAME_MIN, FRAME_MAX = 1, 8
FRAME_MIN_SAMPLE = 5  # その枠番のレース数がこれ未満なら使わない

# 騎手戦法統合の重み（v25 で導入）
# 実走脚質予測 = 馬の脚質 × HORSE_STYLE_WEIGHT + 騎手戦法 × JOCKEY_STYLE_WEIGHT
# 馬の本来の脚質を主軸にしつつ、騎手の戦法傾向で補正する
HORSE_STYLE_WEIGHT = 0.60
JOCKEY_STYLE_WEIGHT = 0.40
# 騎手戦法統計のウィンドウ
JOCKEY_STYLE_WINDOW_MONTHS = 24
JOCKEY_STYLE_MIN_RIDES = 30  # これ未満は信頼度低として騎手補正しない

# 騎手戦法→脚質マッピング（passing_4 ベース）
# pace_handicap の RUNNER_TYPE_THRESHOLDS と整合
# 騎手の {escape, leader, mid, closer} 比率から最も走りそうな脚質を予測する


def _classify_runner_type(avg_relative_pos: float) -> str:
    for runner_type, (low, high) in RUNNER_TYPE_THRESHOLDS.items():
        if low <= avg_relative_pos < high:
            return runner_type
    return "closer"


def _position_score(pos: int, head_count: int) -> float:
    """着順を 0-100 に変換（1着=100, 最下位=0）。"""
    if head_count <= 1:
        return 100.0
    return max(0.0, (head_count - pos) / (head_count - 1) * 100.0)


class PaceHandicapCalculator(IndexCalculator):
    """展開ハンデ指数算出 Agent。

    レース全体の展開予測と各馬の脚質・枠番の適合度を統合し、
    「展開的有利度」を 0-100 で返す。
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)
        self._meet_bias = MeetBiasService(db)
        self._course_features: dict[str, SimpleNamespace] | None = None
        self._first3f_medians: dict[int, float] | None = None
        self._last3f_avg_cache: dict[tuple[str, int, str], float | None] = {}
        self._frame_stats_cache: dict[tuple[str, int, str], dict[int, dict[str, float]]] = {}
        # 騎手戦法統計キャッシュ（jockey_id → 4要素ベクトル, または None）
        self._jockey_style_cache: dict[int, tuple[float, float, float, float] | None] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の展開ハンデ指数を算出する。"""
        result = await self.calculate_batch(race_id)
        val = result.get(horse_id)
        return SPEED_INDEX_MEAN if val is None else val

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        """レース全馬の展開ハンデ指数を一括算出する。"""
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries_result = await self.db.execute(
            select(RaceEntry).where(RaceEntry.race_id == race_id)
        )
        entries = list(entries_result.scalars().all())
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]
        frame_map: dict[int, int | None] = {
            e.horse_id: (int(e.frame_number) if e.frame_number is not None else None)
            for e in entries
        }
        jockey_map: dict[int, int | None] = {
            e.horse_id: e.jockey_id for e in entries
        }

        # Step 1: 全馬の過去成績を一括取得
        rows_map = await self._get_past_results_batch(horse_ids, race.date, race_id)

        # Step 2: 各馬の馬の本来の脚質判定
        horse_runner_types: dict[int, str] = {
            hid: self._determine_runner_type(rows_map.get(hid, []))
            for hid in horse_ids
        }

        # Step 2b: 騎手戦法を取得して「実走脚質予測」に変換 (v25)
        # 馬の脚質 × HORSE_WEIGHT + 騎手戦法 × JOCKEY_WEIGHT で予測される脚質
        runner_types: dict[int, str] = {}
        for hid in horse_ids:
            jid = jockey_map.get(hid)
            jockey_style = await self._get_jockey_style(jid) if jid else None
            runner_types[hid] = self._predict_actual_runner_type(
                horse_type=horse_runner_types[hid],
                jockey_style=jockey_style,
            )

        # Step 3: レース予想ペース（連続スコア → fast/normal/slow）
        pace_type = self._predict_pace(runner_types)

        # Step 4: コース特性・開催バイアス・枠番統計を1回ずつ取得
        course_feat = await self._get_course_features(race.course)
        meet_bias = await self._meet_bias.get_bias(race)
        head_count = race.head_count or len(horse_ids)
        await self._ensure_first3f_medians()
        frame_stats = await self._get_frame_stats(
            race.course, race.distance or 0, race.surface or ""
        )

        # Step 5: 各馬のスコア合算
        result: dict[int, float | None] = {}
        for hid in horse_ids:
            runner_type = runner_types[hid]
            base = PACE_SCORE_TABLE[runner_type][pace_type]
            past = rows_map.get(hid, [])

            score = base
            score = await self._apply_last3f_bonus(score, past, race)
            score = self._apply_course_adjustment(score, runner_type, course_feat)
            score = self._apply_meet_bias_adjustment(
                score, runner_type, frame_map.get(hid), meet_bias
            )
            score = self._apply_field_size_adjustment(
                score, runner_type, head_count, course_feat
            )
            score = self._apply_frame_stats_adjustment(score, frame_map.get(hid), frame_stats)
            score = self._apply_pace_rebound_bonus(score, past)

            result[hid] = round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)

        return result

    # ------------------------------------------------------------------
    # データ取得
    # ------------------------------------------------------------------

    async def _get_past_results_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, list[Any]]:
        """複数馬の過去レース結果を単一クエリで一括取得する。"""
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

    async def _get_course_features(self, course_code: str) -> SimpleNamespace | None:
        if self._course_features is None:
            res = await self.db.execute(select(RacecourseFeatures))
            self._course_features = {
                r.course_code: SimpleNamespace(
                    straight_distance=r.straight_distance,
                    corner_tightness=r.corner_tightness,
                    start_to_corner_m=r.start_to_corner_m,
                )
                for r in res.scalars().all()
            }
        return self._course_features.get(course_code)

    async def _ensure_first3f_medians(self) -> None:
        if self._first3f_medians is not None:
            return
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

    async def _get_frame_stats(
        self, course: str, distance: int, surface: str
    ) -> dict[int, dict[str, float]]:
        """枠番別の勝率統計を取得（同セッションキャッシュ付き）。"""
        cache_key = (course, distance, surface)
        if cache_key in self._frame_stats_cache:
            return self._frame_stats_cache[cache_key]

        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                Race.course == course,
                Race.distance == distance,
                Race.surface == surface,
                RaceResult.finish_position.isnot(None),
                RaceResult.frame_number.isnot(None),
                RaceResult.abnormality_code == 0,
            )
        )
        rows = (await self.db.execute(stmt)).all()

        bucket: dict[int, list[dict[str, float]]] = defaultdict(list)
        for row in rows:
            r: RaceResult = row.RaceResult
            ra: Race = row.Race
            f = r.frame_number
            if f is None or not (FRAME_MIN <= f <= FRAME_MAX) or r.finish_position is None:
                continue
            hc = ra.head_count or 16
            bucket[f].append(
                {
                    "pos_score": _position_score(int(r.finish_position), hc),
                    "is_win": 1.0 if int(r.finish_position) == 1 else 0.0,
                }
            )

        stats: dict[int, dict[str, float]] = {}
        for f, lst in bucket.items():
            n = len(lst)
            if n < FRAME_MIN_SAMPLE:
                continue
            stats[f] = {
                "avg_pos_score": sum(d["pos_score"] for d in lst) / n,
                "win_rate": sum(d["is_win"] for d in lst) / n,
                "cnt": float(n),
            }
        self._frame_stats_cache[cache_key] = stats
        return stats

    async def _get_avg_last3f(
        self, course: str, distance: int, surface: str
    ) -> float | None:
        key = (course, distance, surface)
        if key in self._last3f_avg_cache:
            return self._last3f_avg_cache[key]
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
        row = (await self.db.execute(stmt)).first()
        if row is None or row.cnt is None or int(row.cnt) < MIN_LAST3F_SAMPLE:
            self._last3f_avg_cache[key] = None
            return None
        avg = float(row.avg_last3f) if row.avg_last3f else None
        self._last3f_avg_cache[key] = avg
        return avg

    # ------------------------------------------------------------------
    # 判定ロジック
    # ------------------------------------------------------------------

    def _determine_runner_type(self, rows: list[Any]) -> str:
        """過去レースの平均通過4C位置から脚質を判定する（馬の本来の脚質）。"""
        positions: list[float] = []
        for row in rows:
            r: RaceResult = row.RaceResult
            ra: Race = row.Race
            if r.passing_4 is None or ra.head_count is None or ra.head_count <= 0:
                continue
            positions.append(r.passing_4 / ra.head_count)
        if not positions:
            return "unknown"
        return _classify_runner_type(sum(positions) / len(positions))

    async def _get_jockey_style(
        self, jockey_id: int
    ) -> tuple[float, float, float, float] | None:
        """騎手戦法統計を取得する（escape, leader, mid, closer の比率タプル）。

        keiba.jockey_running_style_stats から WINDOW=24ヶ月のデータを引く。
        サンプル不足（< MIN_RIDES）または未集計の騎手は None を返す。
        セッション内キャッシュ付き。
        """
        if jockey_id in self._jockey_style_cache:
            return self._jockey_style_cache[jockey_id]
        stmt = select(JockeyRunningStyleStats).where(
            JockeyRunningStyleStats.jockey_id == jockey_id,
            JockeyRunningStyleStats.window_months == JOCKEY_STYLE_WINDOW_MONTHS,
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None or (row.total_rides or 0) < JOCKEY_STYLE_MIN_RIDES:
            self._jockey_style_cache[jockey_id] = None
            return None
        result = (
            float(row.escape_rate or 0),
            float(row.leader_rate or 0),
            float(row.mid_rate or 0),
            float(row.closer_rate or 0),
        )
        self._jockey_style_cache[jockey_id] = result
        return result

    @staticmethod
    def _predict_actual_runner_type(
        horse_type: str,
        jockey_style: tuple[float, float, float, float] | None,
    ) -> str:
        """馬の脚質と騎手戦法を統合した「実走脚質」を予測する（v25）。

        馬の脚質の one-hot ベクトル × HORSE_STYLE_WEIGHT
        + 騎手戦法ベクトル          × JOCKEY_STYLE_WEIGHT
        の重み付き和の最大成分を採用する。

        騎手戦法データがない場合は馬の脚質をそのまま返す。
        馬の脚質が unknown のときは騎手戦法のみで判定する。
        """
        if jockey_style is None:
            return horse_type

        # 馬の脚質を one-hot 化
        horse_vec = {
            "escape": (1.0, 0.0, 0.0, 0.0),
            "leader": (0.0, 1.0, 0.0, 0.0),
            "mid":    (0.0, 0.0, 1.0, 0.0),
            "closer": (0.0, 0.0, 0.0, 1.0),
            "unknown": (0.25, 0.25, 0.25, 0.25),  # 中立分布
        }.get(horse_type, (0.25, 0.25, 0.25, 0.25))

        # 馬 unknown のときは騎手寄りに強くする
        h_w = HORSE_STYLE_WEIGHT if horse_type != "unknown" else 0.20
        j_w = 1.0 - h_w

        merged = tuple(
            horse_vec[i] * h_w + jockey_style[i] * j_w for i in range(4)
        )
        labels = ("escape", "leader", "mid", "closer")
        return labels[merged.index(max(merged))]

    def _predict_pace(self, runner_types: dict[int, str]) -> str:
        """脚質分布から予想ペースを判定する。

        従来の「逃げ馬数 only」ではなく、escape + leader の連続スコアを使う。
        - escape 2頭以上 → fast (確実にハイペース)
        - escape 1頭 + leader 3頭以上 → fast (前争い激化)
        - escape 1頭 + leader 少 → normal
        - escape 0 + leader 多 → normal (内乱)
        - escape 0 + leader 少 → slow
        """
        escape = sum(1 for rt in runner_types.values() if rt == "escape")
        leader = sum(1 for rt in runner_types.values() if rt == "leader")
        indicator = escape + leader * 0.5

        if indicator >= PACE_INDICATOR_FAST:
            return "fast"
        if indicator <= PACE_INDICATOR_SLOW:
            return "slow"
        return "normal"

    # ------------------------------------------------------------------
    # 補正
    # ------------------------------------------------------------------

    async def _apply_last3f_bonus(
        self, score: float, past: list[Any], race: Race
    ) -> float:
        """馬の上がり3F平均が同条件平均より速ければ +5。"""
        if not past:
            return score
        vals = [float(r.RaceResult.last_3f) for r in past if r.RaceResult.last_3f is not None]
        if not vals:
            return score
        horse_avg = sum(vals) / len(vals)
        cond_avg = await self._get_avg_last3f(
            race.course, race.distance or 0, race.surface or ""
        )
        if cond_avg is None:
            return score
        if horse_avg < cond_avg:
            return score + LAST_3F_BONUS
        return score

    def _apply_course_adjustment(
        self, score: float, runner_type: str, feat: SimpleNamespace | None
    ) -> float:
        if feat is None:
            return score
        straight = float(feat.straight_distance) if feat.straight_distance else 0.0
        tightness = float(feat.corner_tightness) if feat.corner_tightness else 0.5

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
        frame_number: int | None,
        bias,
    ) -> float:
        """当開催前後バイアス（脚質別）+ 内外バイアス（枠番別）の補正。"""
        # 前後バイアス: front_back > 0 = 前有利
        fb = bias.front_back
        if abs(fb) >= 0.05:
            if runner_type in ("escape", "leader"):
                score += fb * MEET_ADJ_MAX
            elif runner_type in ("mid", "closer"):
                score -= fb * MEET_ADJ_MAX

        # 内外バイアス: inner_outer > 0 = 内有利
        io = bias.inner_outer
        if abs(io) >= 0.05 and frame_number is not None:
            if frame_number <= 4:
                score += io * MEET_ADJ_MAX * 0.7
            else:
                score -= io * MEET_ADJ_MAX * 0.7
        return score

    def _apply_field_size_adjustment(
        self,
        score: float,
        runner_type: str,
        head_count: int,
        feat: SimpleNamespace | None,
    ) -> float:
        if head_count < LARGE_FIELD or feat is None:
            return score

        tightness = float(feat.corner_tightness) if feat.corner_tightness else 0.5
        start_to_corner = int(feat.start_to_corner_m) if feat.start_to_corner_m else 200
        straight = float(feat.straight_distance) if feat.straight_distance else 0.0
        ratio = min(1.0, (head_count - LARGE_FIELD) / 4)

        if tightness >= TIGHT_CORNER:
            if runner_type == "escape":
                score -= ratio * FIELD_ADJ_MAX * 0.8
            elif runner_type == "leader":
                score -= ratio * FIELD_ADJ_MAX * 0.3

        if start_to_corner <= SHORT_START_CORNER:
            if runner_type in ("escape", "leader"):
                score -= ratio * FIELD_ADJ_MAX * 0.5

        if straight >= LONG_STRAIGHT_M:
            if runner_type in ("closer", "mid"):
                score += ratio * FIELD_ADJ_MAX * 0.5
        return score

    def _apply_frame_stats_adjustment(
        self,
        score: float,
        frame_number: int | None,
        stats: dict[int, dict[str, float]],
    ) -> float:
        """枠番統計（過去同条件の枠別勝率偏差）から補正する。"""
        if frame_number is None or not stats:
            return score
        fs = stats.get(frame_number)
        if fs is None:
            return score

        all_avg_pos = [s["avg_pos_score"] for s in stats.values()]
        if len(all_avg_pos) < 2:
            return score
        global_mean = sum(all_avg_pos) / len(all_avg_pos)
        var = sum((x - global_mean) ** 2 for x in all_avg_pos) / len(all_avg_pos)
        std = var**0.5
        if std < 0.5:
            return score

        z = (fs["avg_pos_score"] - global_mean) / std
        # z を [-1.5, +1.5] にクリップして、最大 ±FRAME_ADJ_MAX 点
        z_clipped = max(-1.5, min(1.5, z))
        return score + z_clipped / 1.5 * FRAME_ADJ_MAX

    def _apply_pace_rebound_bonus(self, score: float, prev_rows: list[Any]) -> float:
        """前走 先行×ハイペースだった馬に +6。市場過小評価ケース。"""
        if not prev_rows or self._first3f_medians is None:
            return score
        latest = prev_rows[0]
        r: RaceResult = latest.RaceResult
        ra: Race = latest.Race
        if r.passing_1 is None or ra.first_3f is None or ra.distance is None or ra.head_count is None:
            return score
        if ra.head_count <= 0:
            return score
        was_front = float(r.passing_1) / float(ra.head_count) <= FRONT_POSITION_RATIO
        if not was_front:
            return score
        median = self._first3f_medians.get(int(ra.distance))
        if median is None:
            return score
        if float(ra.first_3f) < median:
            score += PACE_REBOUND_BONUS
        return score
