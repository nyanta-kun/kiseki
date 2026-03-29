"""馬情報APIルーター

馬ごとの近走成績・指数推移を返すエンドポイント。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.models import CalculatedIndex, Horse, NetkeibaRaceExtra, Race, RaceResult
from ..db.session import get_db
from ..indices.composite import COMPOSITE_VERSION

router = APIRouter(prefix="/api/horses", tags=["horses"])

DbDep = Annotated[Session, Depends(get_db)]


class RaceHistoryEntry(BaseModel):
    """近走1レース分のデータ。"""
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
    remarks: str | None


@router.get("/{horse_id}/history")
def get_horse_history(horse_id: int, db: DbDep) -> list[RaceHistoryEntry]:
    """馬の近走成績と指数推移を返す（最新5走）。"""
    horse = db.query(Horse).filter(Horse.id == horse_id).first()
    if not horse:
        raise HTTPException(status_code=404, detail="Horse not found")

    rows = (
        db.query(RaceResult, Race)
        .join(Race, RaceResult.race_id == Race.id)
        .filter(RaceResult.horse_id == horse_id)
        .filter(RaceResult.finish_position.isnot(None))
        .order_by(Race.date.desc())
        .limit(5)
        .all()
    )

    race_ids = [race.id for _, race in rows]
    indices: dict[int, CalculatedIndex] = {}
    extras: dict[int, NetkeibaRaceExtra] = {}
    if race_ids:
        ci_rows = (
            db.query(CalculatedIndex)
            .filter(
                CalculatedIndex.race_id.in_(race_ids),
                CalculatedIndex.horse_id == horse_id,
                CalculatedIndex.version == COMPOSITE_VERSION,
            )
            .all()
        )
        indices = {ci.race_id: ci for ci in ci_rows}

        extra_rows = (
            db.query(NetkeibaRaceExtra)
            .filter(
                NetkeibaRaceExtra.race_id.in_(race_ids),
                NetkeibaRaceExtra.horse_id == horse_id,
            )
            .all()
        )
        extras = {ex.race_id: ex for ex in extra_rows}

    return [
        RaceHistoryEntry(
            date=race.date,
            course_name=race.course_name,
            surface=race.surface,
            distance=race.distance,
            race_name=race.race_name,
            finish_position=rr.finish_position,
            finish_time=float(rr.finish_time) if rr.finish_time else None,
            last_3f=float(rr.last_3f) if rr.last_3f else None,
            horse_number=rr.horse_number,
            win_odds=float(rr.win_odds) if rr.win_odds else None,
            win_popularity=rr.win_popularity,
            composite_index=float(indices[race.id].composite_index) if race.id in indices else None,
            remarks=extras[race.id].remarks if race.id in extras else None,
        )
        for rr, race in rows
    ]
