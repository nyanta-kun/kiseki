"""地方競馬 馬情報APIルーター"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouHorse,
    ChihouRace,
    ChihouRaceResult,
)
from ..db.session import get_db
from ..indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

router = APIRouter(prefix="/api/chihou/horses", tags=["chihou-horses"])
DbDep = Annotated[AsyncSession, Depends(get_db)]


class ChihouRaceHistoryEntry(BaseModel):
    """地方競馬 近走1レース分のデータ。"""

    date: str
    course_name: str
    surface: str
    distance: int
    race_name: str | None
    finish_position: int | None
    finish_time: float | None
    last_3f: float | None
    horse_number: int | None
    win_odds: float | None
    win_popularity: int | None
    composite_index: float | None
    remarks: None = None  # フロントエンドの RaceHistoryEntry 型との互換性のため


@router.get("/{horse_id}/history")
async def get_chihou_horse_history(horse_id: int, db: DbDep) -> list[ChihouRaceHistoryEntry]:
    """地方競馬 馬の近走成績と指数推移を返す（最新5走）。"""
    horse_result = await db.execute(select(ChihouHorse).where(ChihouHorse.id == horse_id))
    horse = horse_result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Horse not found")

    stmt = (
        select(ChihouRaceResult, ChihouRace)
        .join(ChihouRace, ChihouRaceResult.race_id == ChihouRace.id)
        .where(ChihouRaceResult.horse_id == horse_id)
        .where(ChihouRaceResult.finish_position.isnot(None))
        .order_by(ChihouRace.date.desc())
        .limit(5)
    )
    rows_result = await db.execute(stmt)
    rows = rows_result.all()

    race_ids = [race.id for _, race in rows]
    indices: dict[int, ChihouCalculatedIndex] = {}
    if race_ids:
        ci_result = await db.execute(
            select(ChihouCalculatedIndex).where(
                ChihouCalculatedIndex.race_id.in_(race_ids),
                ChihouCalculatedIndex.horse_id == horse_id,
                ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION,
            )
        )
        ci_rows = ci_result.scalars().all()
        indices = {ci.race_id: ci for ci in ci_rows}

    return [
        ChihouRaceHistoryEntry(
            date=race.date,
            course_name=race.course_name,
            surface=race.surface or "",
            distance=race.distance or 0,
            race_name=race.race_name,
            finish_position=rr.finish_position,
            finish_time=float(rr.finish_time) if rr.finish_time is not None else None,
            last_3f=float(rr.last_3f) if rr.last_3f is not None else None,
            horse_number=rr.horse_number,
            win_odds=float(rr.win_odds) if rr.win_odds is not None else None,
            win_popularity=rr.win_popularity,
            composite_index=(
                float(indices[race.id].composite_index)
                if race.id in indices and indices[race.id].composite_index is not None
                else None
            ),
        )
        for rr, race in rows
    ]
