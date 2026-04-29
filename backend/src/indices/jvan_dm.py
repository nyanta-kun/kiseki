"""JV-Next DM (タイム型・対戦型) 指数 Calculator

JRA-VAN NEXT が提供する DM (DataMining) 指数を直接利用する。
- jvan_time_dm: タイム型指数 (走破タイムベース、平均約50-60、レンジ0-100)
- jvan_battle_dm: 対戦型指数 (相手評価込み、平均約50-60、レンジ0-100)

これらは race_entries テーブルに 1403 ファイルから importer 経由で格納される。
直接の値をそのまま指数として返すシンプル実装。

Why:
    JRA-VAN NEXT の DM はプロが信頼する確立した指数。我々の独自 19 指数と
    別系統の情報源として組み込むことで、より多角的な評価が可能。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..db.models import RaceEntry
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator

logger = logging.getLogger(__name__)


class JvanTimeDmCalculator(IndexCalculator):
    """JV-Next タイム型DM 指数。

    race_entries.jvan_time_dm をそのまま指数値として返す。
    値が存在しない (NULL) 馬は SPEED_INDEX_MEAN (=50.0) で補完。
    """

    async def calculate(self, race_id: int, horse_id: int) -> float:
        stmt = select(RaceEntry.jvan_time_dm).where(
            RaceEntry.race_id == race_id,
            RaceEntry.horse_id == horse_id,
        )
        result = await self.db.execute(stmt)
        val = result.scalar_one_or_none()
        if val is None:
            return SPEED_INDEX_MEAN
        return float(val)

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        stmt = select(RaceEntry.horse_id, RaceEntry.jvan_time_dm).where(
            RaceEntry.race_id == race_id,
        )
        result = await self.db.execute(stmt)
        out: dict[int, float | None] = {}
        for horse_id, val in result.all():
            out[horse_id] = float(val) if val is not None else None
        return out


class JvanBattleDmCalculator(IndexCalculator):
    """JV-Next 対戦型DM 指数。

    race_entries.jvan_battle_dm をそのまま指数値として返す。
    値が存在しない (NULL) 馬は SPEED_INDEX_MEAN (=50.0) で補完。
    """

    async def calculate(self, race_id: int, horse_id: int) -> float:
        stmt = select(RaceEntry.jvan_battle_dm).where(
            RaceEntry.race_id == race_id,
            RaceEntry.horse_id == horse_id,
        )
        result = await self.db.execute(stmt)
        val = result.scalar_one_or_none()
        if val is None:
            return SPEED_INDEX_MEAN
        return float(val)

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        stmt = select(RaceEntry.horse_id, RaceEntry.jvan_battle_dm).where(
            RaceEntry.race_id == race_id,
        )
        result = await self.db.execute(stmt)
        out: dict[int, float | None] = {}
        for horse_id, val in result.all():
            out[horse_id] = float(val) if val is not None else None
        return out
