"""調教インポーターの分割 UPSERT ユニットテスト

asyncpg のバインドパラメータ上限（32,767）を1文で超えないことを確認する。
DBアクセスは Mock。

背景: 2026-08-24 06:00 に `POST /api/import/training` が 500 を返していた。
Windows Agent は 2,000件ずつ POST し、SLOP/WOOD は別 DataSpec なので
バッチは均質になる。wood は 25列あるので 2,000行 = 50,000 パラメータで上限超過。
実測では WOOD 4,141件のうち [0:2000] と [2000:4000] が落ち、末尾141件だけ入った。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.db.models import SlopeTraining, WoodTraining
from src.importers.training_importer import (
    _MAX_BIND_PARAMS,
    _SLOPE_COLS,
    _WOOD_COLS,
    TrainingImporter,
)


def _rows(cols: tuple[str, ...], n: int) -> list[dict]:
    return [{c: f"v{i}" for c in cols} for i in range(n)]


class TestUpsertChunking:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model", "cols", "n"),
        [
            (WoodTraining, _WOOD_COLS, 2000),   # エージェントの1バッチ
            (WoodTraining, _WOOD_COLS, 4141),   # 2026-08-24 に実際に来た件数
            (SlopeTraining, _SLOPE_COLS, 3000),
        ],
    )
    async def test_no_statement_exceeds_bind_limit(
        self, model: type, cols: tuple[str, ...], n: int
    ) -> None:
        """どの1文もバインドパラメータ上限を超えない。"""
        db = AsyncMock()
        imp = TrainingImporter(db)

        assert await imp._upsert(model, _rows(cols, n), cols) == n

        assert db.execute.await_count >= 1
        for call in db.execute.await_args_list:
            stmt = call.args[0]
            n_rows = len(stmt.compile().params) // len(cols)
            assert n_rows * len(cols) <= _MAX_BIND_PARAMS

    @pytest.mark.asyncio
    async def test_all_rows_are_sent(self) -> None:
        """分割しても行が落ちない（合計が一致する）。"""
        db = AsyncMock()
        imp = TrainingImporter(db)
        n = 4141

        await imp._upsert(WoodTraining, _rows(_WOOD_COLS, n), _WOOD_COLS)

        sent = sum(
            len(c.args[0].compile().params) // len(_WOOD_COLS)
            for c in db.execute.await_args_list
        )
        assert sent == n

    @pytest.mark.asyncio
    async def test_empty_is_noop(self) -> None:
        db = AsyncMock()
        imp = TrainingImporter(db)
        assert await imp._upsert(WoodTraining, [], _WOOD_COLS) == 0
        db.execute.assert_not_awaited()
