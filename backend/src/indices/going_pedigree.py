"""重馬場×血統指数算出Agent（Going × Pedigree Index）

馬場状態が重または不良の場合に、各馬の父系統が
重馬場でどれだけ好成績を残しているかをスコア化する。

算出ロジック:
  1. 現在レースの馬場状態 (Race.condition) を取得
  2. '良' または '稍': 全馬に DEFAULT_SCORE(50.0) を返す（方向性なし）
  3. '重' または '不' の場合のみスコア算出:
       - 各馬の父馬名 (Pedigree.sire) を取得
       - 父馬ごとに産駒の重馬場（'重'/'不'）成績を集計:
           heavy_win_rate = 重/不馬場での勝利数 / 重/不馬場での出走数
           overall_win_rate = 全成績での勝利数 / 全出走数
       - affinity = heavy_win_rate / max(overall_win_rate, 0.01)
       - score = clip(50 + (affinity - 1.0) × 20, 25, 75)
  4. 重/不馬場での産駒出走数 < 10 の場合は DEFAULT_SCORE(50.0)
  5. データなし時は DEFAULT_SCORE(50.0)

スコアは 25〜75 にキャップ（単一要因のため極端なスコアを抑制）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Pedigree, Race, RaceEntry, RaceResult
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 重馬場として扱う馬場状態
HEAVY_CONDITIONS = frozenset(["重", "不"])
# 良/稍馬場（中立）
NEUTRAL_CONDITIONS = frozenset(["良", "稍"])
# 重馬場での産駒出走数の最小要件
MIN_HEAVY_RACES = 10
# デフォルトスコア（中立）
DEFAULT_SCORE = 50.0
# スコア変換係数（affinity=2.0 → +20点）
AFFINITY_SCALE = 20.0
# スコアクリップ範囲（方向性の強い外れ値を抑制）
CLIP_MIN = 25.0
CLIP_MAX = 75.0


class GoingPedigreeIndexCalculator(IndexCalculator):
    """重馬場×血統指数算出Agent。

    馬場状態が重/不良の場合に、父馬の産駒が重馬場で
    どれだけ好成績を残しているかをスコア化する。
    良/稍馬場や重馬場でのデータが不足する場合は
    中立値（50.0）を返す。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        super().__init__(db)

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の重馬場×血統指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            重馬場×血統指数（0-100, 中立=50）。データ不足時は DEFAULT_SCORE。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return DEFAULT_SCORE

        # 良/稍馬場は中立
        if race.condition not in HEAVY_CONDITIONS:
            return DEFAULT_SCORE

        batch = await self._compute_batch([horse_id], race)
        return batch.get(horse_id, DEFAULT_SCORE)

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        """レース全馬の重馬場×血統指数を一括算出する。

        N+1 を回避するため、全馬のデータを単一または少数のクエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: going_pedigree_index} の dict。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return {}

        entries_result = await self.db.execute(
            select(RaceEntry).where(RaceEntry.race_id == race_id)
        )
        entries = entries_result.scalars().all()
        if not entries:
            return {}

        # 良/稍馬場は全馬データなし扱い
        if race.condition not in HEAVY_CONDITIONS:
            return {e.horse_id: None for e in entries}

        horse_ids = [e.horse_id for e in entries]
        return await self._compute_batch(horse_ids, race)

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _compute_batch(
        self,
        horse_ids: list[int],
        current_race: Race,
    ) -> dict[int, float | None]:
        """複数馬の重馬場×血統指数を一括算出する（重/不馬場のみ呼び出し）。

        Args:
            horse_ids: 対象馬 ID リスト
            current_race: 現在の Race オブジェクト

        Returns:
            {horse_id: going_pedigree_index} の dict。
        """
        if not horse_ids:
            return {}

        before_date = current_race.date

        # ----------------------------------------------------------------
        # Step 1: 各馬の父馬名を一括取得
        # ----------------------------------------------------------------
        pedigree_result = await self.db.execute(
            select(Pedigree.horse_id, Pedigree.sire).where(
                Pedigree.horse_id.in_(horse_ids)
            )
        )
        sire_map: dict[int, str | None] = {
            row.horse_id: row.sire for row in pedigree_result.all()
        }

        # ユニークな父馬名を収集
        unique_sires: set[str] = {
            sire
            for sire in sire_map.values()
            if sire is not None
        }

        if not unique_sires:
            return {hid: None for hid in horse_ids}

        # ----------------------------------------------------------------
        # Step 2: 父馬ごとに産駒の成績を一括取得
        # ----------------------------------------------------------------
        # (sire -> heavy_wins, heavy_total, all_wins, all_total)
        sire_stats: dict[str, tuple[int, int, int, int]] = {}

        for sire in unique_sires:
            stmt = (
                select(
                    Race.condition,
                    RaceResult.finish_position,
                )
                .join(Race, RaceResult.race_id == Race.id)
                .join(Pedigree, Pedigree.horse_id == RaceResult.horse_id)
                .where(
                    Pedigree.sire == sire,
                    Race.date < before_date,
                    RaceResult.abnormality_code == 0,
                    RaceResult.finish_position.is_not(None),
                )
            )
            rows = (await self.db.execute(stmt)).all()

            heavy_wins = 0
            heavy_total = 0
            all_wins = 0
            all_total = 0
            for row in rows:
                cond = row.condition
                fp = int(row.finish_position)
                all_total += 1
                if fp == 1:
                    all_wins += 1
                if cond in HEAVY_CONDITIONS:
                    heavy_total += 1
                    if fp == 1:
                        heavy_wins += 1

            sire_stats[sire] = (heavy_wins, heavy_total, all_wins, all_total)

        # ----------------------------------------------------------------
        # Step 3: スコア算出
        # ----------------------------------------------------------------
        result: dict[int, float | None] = {}
        for hid in horse_ids:
            sire_or_none = sire_map.get(hid)
            if sire_or_none is None:
                result[hid] = None
                continue

            horse_sire: str = sire_or_none
            stats = sire_stats.get(horse_sire)
            if stats is None:
                result[hid] = None
                continue

            heavy_wins, heavy_total, all_wins, all_total = stats
            if heavy_total < MIN_HEAVY_RACES:
                result[hid] = None
                continue

            heavy_win_rate = heavy_wins / heavy_total
            overall_win_rate = all_wins / max(all_total, 1)
            affinity = heavy_win_rate / max(overall_win_rate, 0.01)
            raw = 50.0 + (affinity - 1.0) * AFFINITY_SCALE
            score = max(CLIP_MIN, min(CLIP_MAX, raw))
            result[hid] = round(score, 1)

        return result
