"""枠順バイアス指数算出Agent

同コース・同距離・同馬場の過去全レース結果から枠番別の勝率・平均着順を集計し、
対象馬の枠番が有利か不利かをスコア化する。

算出ロジック:
  1. 対象レースのコース・距離・馬場で過去レース結果を全件集計（長期統計）
  2. 枠番(1-8)ごとに「平均着順」「勝率（1着率）」を計算
  3. 全枠の平均着順・平均勝率を基準に、対象枠番の有利不利を算出
  4. 平均=50, σ=10 に正規化 (同じスケール)
  5. サンプル不足（MIN_SAMPLE 未満）は 50 を返す
  6. 当開催バイアス（MeetBiasService）で補正する
     - 長期統計 70% + 当開催バイアス 30%
     - 当開催で内有利が続いていれば内枠スコアを加算、外有利なら外枠スコアを加算

スコアイメージ:
  - 内枠有利コースで1枠（かつ当開催も内有利）: 65前後
  - 外枠不利コースで8枠（かつ当開催も外有利）: 35前後
  - データ平均的な枠番: 50前後
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN, SPEED_INDEX_STD
from .base import IndexCalculator
from .meet_bias import MeetBiasService

logger = logging.getLogger(__name__)

# 枠番別統計に必要な最低サンプル数（その枠番のレース数）
MIN_SAMPLE = 5
# 過去を遡る年数上限（無制限だとデータが古すぎる問題があるため）
MAX_YEARS_BACK = 5
# 指数クリップ
INDEX_MIN = 0.0
INDEX_MAX = 100.0
# 枠番範囲
FRAME_MIN = 1
FRAME_MAX = 8
# 長期統計 vs 当開催バイアスの合成比率
LONG_TERM_WEIGHT = 0.70
MEET_BIAS_WEIGHT = 0.30
# 当開催バイアスの最大影響量（ポイント）
MEET_BIAS_MAX_ADJ = 8.0


# 着順→スコア変換: 1着に近いほど高いスコア
def _position_score(pos: int, head_count: int) -> float:
    """着順を0-100のスコアに変換する（頭数で正規化）。

    Args:
        pos: 着順（1始まり）
        head_count: 出走頭数

    Returns:
        0-100のスコア（1着=100, 最下位=0）
    """
    if head_count <= 1:
        return 100.0
    return max(0.0, (head_count - pos) / (head_count - 1) * 100.0)


class FrameBiasCalculator(IndexCalculator):
    """枠順バイアス指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        super().__init__(db)
        # 枠番統計のキャッシュ（同セッション内で再利用）
        # key: (course, distance, surface) -> {frame_number: {"avg_pos": float, "win_rate": float, "cnt": int}}
        self._frame_stats_cache: dict[tuple[str, int, str], dict[int, dict[str, float]]] = {}
        self._meet_bias = MeetBiasService(db)

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の枠順バイアス指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            枠順バイアス指数（0-100, 平均50）。データ不足時は SPEED_INDEX_MEAN。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        entry_result = await self.db.execute(
            select(RaceEntry).where(
                RaceEntry.race_id == race_id,
                RaceEntry.horse_id == horse_id,
            )
        )
        entry = entry_result.scalar_one_or_none()
        if not entry or entry.frame_number is None:
            logger.warning(
                f"Entry not found or no frame_number: race_id={race_id}, horse_id={horse_id}"
            )
            return SPEED_INDEX_MEAN

        return await self._compute_frame_bias(race, int(entry.frame_number))

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の枠順バイアス指数を一括算出する。

        枠番統計は1回だけ取得してキャッシュを活用する（N+1回避）。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: frame_bias_index} のdict。エントリが存在しない場合は空dict。
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

        result: dict[int, float] = {}
        for entry in entries:
            if entry.frame_number is None:
                result[entry.horse_id] = SPEED_INDEX_MEAN
            else:
                result[entry.horse_id] = await self._compute_frame_bias(race, int(entry.frame_number))

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _compute_frame_bias(self, race: Race, frame_number: int) -> float:
        """指定枠番の枠順バイアス指数を算出する。

        Args:
            race: 対象レース情報
            frame_number: 枠番（1-8）

        Returns:
            枠順バイアス指数（0-100, 平均50）。データ不足時は SPEED_INDEX_MEAN。
        """
        course = race.course
        distance = race.distance or 0
        surface = race.surface or ""

        stats = await self._get_frame_stats(course, distance, surface)
        if not stats:
            return SPEED_INDEX_MEAN

        frame_stat = stats.get(frame_number)
        if frame_stat is None or frame_stat["cnt"] < MIN_SAMPLE:
            return SPEED_INDEX_MEAN

        # 全枠の平均着順スコア・勝率を算出（基準値）
        all_scores = [s["avg_pos_score"] for s in stats.values() if s["cnt"] >= MIN_SAMPLE]
        all_win_rates = [s["win_rate"] for s in stats.values() if s["cnt"] >= MIN_SAMPLE]

        if not all_scores:
            return SPEED_INDEX_MEAN

        global_avg_score = sum(all_scores) / len(all_scores)
        global_avg_win = sum(all_win_rates) / len(all_win_rates) if all_win_rates else 0.0

        # 対象枠番のスコアと勝率
        frame_score = frame_stat["avg_pos_score"]
        frame_win = frame_stat["win_rate"]

        # 全枠標準偏差（枠間のばらつき）
        if len(all_scores) >= 2:
            variance = sum((s - global_avg_score) ** 2 for s in all_scores) / len(all_scores)
            score_std = variance**0.5
        else:
            score_std = 1.0  # フォールバック

        if score_std < 0.1:
            # 枠間の差がほぼない場合は全枠 50
            return SPEED_INDEX_MEAN

        # 着順スコアと勝率を合成（着順スコア 70% + 勝率 30%）
        if global_avg_win > 0:
            win_component = (frame_win - global_avg_win) / global_avg_win * SPEED_INDEX_STD
        else:
            win_component = 0.0

        pos_component = (frame_score - global_avg_score) / score_std * SPEED_INDEX_STD

        long_term_score = SPEED_INDEX_MEAN + pos_component * 0.7 + win_component * 0.3

        # 当開催バイアス補正
        # inner_outer > 0 = 内有利 → 内枠(1-4)を加算、外枠(5+)を減算
        meet_bias = await self._meet_bias.get_bias(race)
        if frame_number <= 4:
            bias_adj = meet_bias.inner_outer * MEET_BIAS_MAX_ADJ
        else:
            bias_adj = -meet_bias.inner_outer * MEET_BIAS_MAX_ADJ

        raw = long_term_score * LONG_TERM_WEIGHT + (long_term_score + bias_adj) * MEET_BIAS_WEIGHT
        return round(max(INDEX_MIN, min(INDEX_MAX, raw)), 1)

    async def _get_frame_stats(
        self, course: str, distance: int, surface: str
    ) -> dict[int, dict[str, float]]:
        """枠番別統計を取得する（キャッシュ付き）。

        Args:
            course: 場コード
            distance: 距離（m）
            surface: 馬場種別（芝/ダ/障）

        Returns:
            {frame_number: {"avg_pos_score": float, "win_rate": float, "cnt": int}}
            データが少ない場合は空dict。
        """
        cache_key = (course, distance, surface)
        if cache_key in self._frame_stats_cache:
            return self._frame_stats_cache[cache_key]

        stats = await self._compute_frame_stats(course, distance, surface)
        self._frame_stats_cache[cache_key] = stats
        return stats

    async def _compute_frame_stats(
        self, course: str, distance: int, surface: str
    ) -> dict[int, dict[str, float]]:
        """DBから枠番別統計を集計する。

        Args:
            course: 場コード
            distance: 距離（m）
            surface: 馬場種別

        Returns:
            {frame_number: {"avg_pos_score": float, "win_rate": float, "cnt": int}}
        """
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
        db_result = await self.db.execute(stmt)
        rows = db_result.all()

        # 枠番ごとに集計
        frame_data: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            result: RaceResult = row.RaceResult
            race: Race = row.Race

            frame = result.frame_number
            if frame is None or not (FRAME_MIN <= frame <= FRAME_MAX) or result.finish_position is None:
                continue

            head_count = race.head_count or 16  # 不明時は16頭とみなす
            pos_score = _position_score(int(result.finish_position), head_count)
            is_win = 1 if int(result.finish_position) == 1 else 0

            frame_data[frame].append(
                {
                    "pos_score": pos_score,
                    "is_win": is_win,
                }
            )

        # 統計値に変換
        stats: dict[int, dict[str, float]] = {}
        for frame, data_list in frame_data.items():
            cnt = len(data_list)
            avg_pos_score = sum(d["pos_score"] for d in data_list) / cnt
            win_rate = sum(d["is_win"] for d in data_list) / cnt
            stats[frame] = {
                "avg_pos_score": avg_pos_score,
                "win_rate": win_rate,
                "cnt": float(cnt),
            }

        return stats
