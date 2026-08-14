"""JRA 推奨（hit_tier）の前向き記録 API（cron から叩く2本）。

POST /api/jra/hit-tier/snapshot            (X-API-Key認証・毎分)
POST /api/jra/hit-tier/settle?date=        (X-API-Key認証・日次)

なぜ記録が要るかは `src/services/jra_hit_tier_log.py` の docstring を参照。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..services.jra_hit_tier_log import settle_hit_tier, snapshot_hit_tier
from .import_router import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jra/hit-tier", tags=["jra-hit-tier"])

ApiKeyDep = Annotated[None, Depends(verify_api_key)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.post("/snapshot")
async def post_snapshot(_: ApiKeyDep, db: DbDep) -> dict[str, int]:
    """発走直前のレースを記録する（毎分 cron）。

    対象が無ければ 0 件を返す。同じレースを二度記録することはない。
    """
    return await snapshot_hit_tier(db)


@router.post("/settle")
async def post_settle(
    _: ApiKeyDep,
    db: DbDep,
    date: str = Query(..., description="対象日 YYYYMMDD"),
) -> dict[str, int]:
    """指定日のスナップショットに確定結果を書き戻す（日次 cron）。冪等。"""
    return await settle_hit_tier(db, date)
