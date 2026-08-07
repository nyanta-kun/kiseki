"""馬体重取込による指数再算出トリガーの検査。

`/api/import/weights` は realtime ループから約30秒ごとに同じ 0B11 を投げられる。
「充足数が増えたレースだけ再算出する」という差分条件が壊れると、
全馬そろったあとも延々と全レースを再算出し続けることになる。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import import_router


def _weights_body(date: str = "20260808"):
    body = MagicMock()
    body.date = date
    body.records = []
    return body


async def _call_import_weights(before: dict[int, int], after: dict[int, int]):
    """import_weights を呼び、BackgroundTasks に積まれた再算出対象を返す。"""
    db = AsyncMock()
    background = MagicMock()
    coverage = iter([before, after])

    with (
        patch.object(
            import_router, "_weight_coverage_by_race", AsyncMock(side_effect=lambda *_: next(coverage))
        ),
        patch.object(import_router, "RaceImporter") as importer_cls,
    ):
        importer_cls.return_value.import_records = AsyncMock(return_value={"entries": 0})
        result = await import_router.import_weights(
            body=_weights_body(), background_tasks=background, _=None, db=db
        )

    scheduled = [c.args for c in background.add_task.call_args_list]
    return result, scheduled


@pytest.mark.asyncio
async def test_races_with_new_weights_are_recalculated():
    """馬体重が0→14頭になったレースは再算出対象に入る。"""
    result, scheduled = await _call_import_weights(
        before={101: 0, 102: 0}, after={101: 14, 102: 0}
    )

    assert result["recalculated_races"] == 1
    assert len(scheduled) == 1
    fn, race_ids = scheduled[0]
    assert fn is import_router._recalculate_races
    assert race_ids == [101]


@pytest.mark.asyncio
async def test_no_recalculation_when_coverage_unchanged():
    """充足数が変わらない再送では再算出を登録しない（30秒ポーリング対策）。"""
    result, scheduled = await _call_import_weights(
        before={101: 14, 102: 16}, after={101: 14, 102: 16}
    )

    assert result["recalculated_races"] == 0
    assert scheduled == []


@pytest.mark.asyncio
async def test_partial_fill_then_completion_recalculates_twice():
    """1レース内で段階的に埋まる場合、増えるたびに再算出する。"""
    _, first = await _call_import_weights(before={101: 0}, after={101: 8})
    _, second = await _call_import_weights(before={101: 8}, after={101: 14})

    assert [c[1] for c in first] == [[101]]
    assert [c[1] for c in second] == [[101]]


@pytest.mark.asyncio
async def test_new_race_appearing_counts_as_increase():
    """スナップショット前に存在しなかったレースも増加として扱う。"""
    result, scheduled = await _call_import_weights(before={}, after={101: 12})

    assert result["recalculated_races"] == 1
    assert scheduled[0][1] == [101]


@pytest.mark.asyncio
async def test_recalculate_races_continues_after_one_failure():
    """1レースの算出が落ちても残りのレースは処理する。"""
    calc = MagicMock()
    calc.calculate_and_save = AsyncMock(side_effect=[RuntimeError("boom"), []])
    session = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(import_router, "AsyncSessionLocal", MagicMock(return_value=ctx)),
        patch.object(import_router, "CompositeIndexCalculator", MagicMock(return_value=calc)),
    ):
        await import_router._recalculate_races([101, 102])

    assert calc.calculate_and_save.await_count == 2
    session.rollback.assert_awaited_once()
