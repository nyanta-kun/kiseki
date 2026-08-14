"""`_get_weight_change_map` の読み先の検査。

🔴 **固定したい挙動**: 馬体重増減は `race_entries` を先に見ること。

`race_results` はレース確定後にしか存在しないため、そこだけを読むと**発走前は
必ず欠損**する。一方で学習側（`train_jra_out_rate.FETCH_SQL`）は
`race_results.weight_change` を読むので常に埋まっている。
これは「学習では効くが配信では常に欠損」という train/serve 不整合であり、
honest test で指数1位馬の勝率 −0.39pt に相当した
（docs/jra_rebuild_2026_08.md 4.6）。

`race_entries.weight_change` は 0B11（速報馬体重・発走の約1時間前）で埋まる。
両表に値がある 70,466 行で差分ゼロを確認済み。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.db.models import RaceEntry, RaceResult
from src.indices.composite import CompositeIndexCalculator


def _rows(pairs: list[tuple[int, int | None]]):
    """`db.execute(...).all()` が返す形を作る。"""
    result = AsyncMock()
    result.all = lambda: pairs
    return result


def _calc_with(responses: list[list[tuple[int, int | None]]]):
    """execute の呼び出し順に応答を返す calculator と、発行された SELECT の記録を返す。"""
    issued: list[object] = []
    it = iter(responses)

    async def _execute(stmt):
        issued.append(stmt)
        return _rows(next(it))

    db = AsyncMock()
    db.execute = _execute
    return CompositeIndexCalculator(db), issued


def _tables(stmt) -> set[str]:
    """SELECT が触れているテーブル名。"""
    return {t.name for t in stmt.get_final_froms()}


@pytest.mark.asyncio
async def test_reads_race_entries_first() -> None:
    """全馬 race_entries に値があれば race_results は引かない。"""
    calc, issued = _calc_with([[(1, 4), (2, -6)]])

    got = await calc._get_weight_change_map(race_id=100, horse_ids=[1, 2])

    assert got == {1: 4, 2: -6}
    assert len(issued) == 1, "race_entries だけで足りるなら追加の SELECT は不要"
    assert _tables(issued[0]) == {RaceEntry.__tablename__}


@pytest.mark.asyncio
async def test_falls_back_to_race_results_for_missing_horses() -> None:
    """race_entries が空の馬だけ race_results を見る（古いデータ向けの保険）。"""
    calc, issued = _calc_with([[(1, 4)], [(2, -6)]])

    got = await calc._get_weight_change_map(race_id=100, horse_ids=[1, 2])

    assert got == {1: 4, 2: -6}
    assert len(issued) == 2
    assert _tables(issued[0]) == {RaceEntry.__tablename__}
    assert _tables(issued[1]) == {RaceResult.__tablename__}


@pytest.mark.asyncio
async def test_null_in_entries_is_treated_as_missing() -> None:
    """race_entries 側が NULL の馬はフォールバック対象に含める。"""
    calc, issued = _calc_with([[(1, None), (2, -6)], [(1, 2)]])

    got = await calc._get_weight_change_map(race_id=100, horse_ids=[1, 2])

    assert got == {1: 2, 2: -6}
    assert len(issued) == 2


@pytest.mark.asyncio
async def test_no_horses_short_circuits() -> None:
    calc, issued = _calc_with([])

    assert await calc._get_weight_change_map(race_id=100, horse_ids=[]) == {}
    assert issued == []
