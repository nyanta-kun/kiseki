"""JvanTimeDmCalculator / JvanBattleDmCalculator のユニットテスト.

DB はモックを使用する。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.indices.jvan_dm import JvanBattleDmCalculator, JvanTimeDmCalculator


def _make_db_for_scalar(value):
    """db.execute → result.scalar_one_or_none() で value を返すモック."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db.execute.return_value = result
    return db


def _make_db_for_pairs(pairs: list[tuple[int, object]]):
    """db.execute → result.all() で [(horse_id, value), ...] を返すモック."""
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = pairs
    db.execute.return_value = result
    return db


# ---------------------------------------------------------------------------
# JvanTimeDmCalculator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_time_dm_returns_value():
    db = _make_db_for_scalar(Decimal("63.5"))
    calc = JvanTimeDmCalculator(db)
    val = await calc.calculate(race_id=1, horse_id=101)
    assert val == 63.5


@pytest.mark.asyncio
async def test_time_dm_returns_default_when_null():
    db = _make_db_for_scalar(None)
    calc = JvanTimeDmCalculator(db)
    val = await calc.calculate(race_id=1, horse_id=101)
    assert val == 50.0  # SPEED_INDEX_MEAN


@pytest.mark.asyncio
async def test_time_dm_batch_with_mix():
    pairs = [
        (101, Decimal("60.0")),
        (102, None),  # データなし
        (103, Decimal("75.5")),
    ]
    db = _make_db_for_pairs(pairs)
    calc = JvanTimeDmCalculator(db)
    out = await calc.calculate_batch(race_id=1)
    assert out == {101: 60.0, 102: None, 103: 75.5}


# ---------------------------------------------------------------------------
# JvanBattleDmCalculator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_battle_dm_returns_value():
    db = _make_db_for_scalar(Decimal("82.4"))
    calc = JvanBattleDmCalculator(db)
    val = await calc.calculate(race_id=1, horse_id=101)
    assert val == 82.4


@pytest.mark.asyncio
async def test_battle_dm_returns_default_when_null():
    db = _make_db_for_scalar(None)
    calc = JvanBattleDmCalculator(db)
    val = await calc.calculate(race_id=1, horse_id=101)
    assert val == 50.0


@pytest.mark.asyncio
async def test_battle_dm_batch_all_null():
    """全頭 NULL のレースでも空dict ではなく {hid: None} が返る."""
    pairs = [(201, None), (202, None)]
    db = _make_db_for_pairs(pairs)
    calc = JvanBattleDmCalculator(db)
    out = await calc.calculate_batch(race_id=1)
    assert out == {201: None, 202: None}
