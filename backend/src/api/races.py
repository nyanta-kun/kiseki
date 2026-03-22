"""レース参照APIルーター

DBに格納済みのレース・出馬表・成績データを返すエンドポイント。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.models import Horse, Jockey, Race, RaceEntry, RaceResult
from ..db.session import get_db

router = APIRouter(prefix="/api/races", tags=["races"])

DbDep = Annotated[Session, Depends(get_db)]


# -------------------------------------------------------------------
# レスポンスモデル
# -------------------------------------------------------------------
class RaceOut(BaseModel):
    """レース情報レスポンス。"""
    id: int
    date: str
    course_name: str
    race_number: int
    race_name: str | None
    surface: str
    distance: int
    grade: str | None
    condition: str | None
    weather: str | None
    jravan_race_id: str | None

    model_config = {"from_attributes": True}


class EntryOut(BaseModel):
    """出馬表エントリーレスポンス。"""
    id: int
    frame_number: int
    horse_number: int
    horse_name: str
    jockey_name: str | None
    trainer_name: str | None
    weight_carried: float | None
    horse_weight: int | None
    weight_change: int | None

    model_config = {"from_attributes": True}


class ResultOut(BaseModel):
    """成績レスポンス。"""
    horse_number: int | None
    finish_position: int | None
    finish_time: float | None
    last_3f: float | None
    horse_name: str

    model_config = {"from_attributes": True}


# -------------------------------------------------------------------
# エンドポイント
# -------------------------------------------------------------------
@router.get("")
def list_races(
    db: DbDep,
    date: str = Query(..., description="対象日付 YYYYMMDD"),
    course: str | None = Query(None, description="場コード (01-10) または場名"),
) -> list[RaceOut]:
    """指定日のレース一覧を返す。"""
    q = db.query(Race).filter(Race.date == date)
    if course:
        if len(course) <= 2 and course.isdigit():
            q = q.filter(Race.course == course)
        else:
            q = q.filter(Race.course_name == course)
    races = q.order_by(Race.race_number).all()
    return [RaceOut.model_validate(r) for r in races]


@router.get("/{race_id}/entries")
def get_entries(race_id: int, db: DbDep) -> list[EntryOut]:
    """レースの出馬表を返す。"""
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    entries = (
        db.query(RaceEntry, Horse, Jockey)
        .join(Horse, RaceEntry.horse_id == Horse.id)
        .outerjoin(Jockey, RaceEntry.jockey_id == Jockey.id)
        .filter(RaceEntry.race_id == race_id)
        .order_by(RaceEntry.horse_number)
        .all()
    )

    result = []
    for entry, horse, jockey in entries:
        result.append(EntryOut(
            id=entry.id,
            frame_number=entry.frame_number,
            horse_number=entry.horse_number,
            horse_name=horse.name,
            jockey_name=jockey.name if jockey else None,
            trainer_name=None,  # Trainerは別途join対応
            weight_carried=float(entry.weight_carried) if entry.weight_carried else None,
            horse_weight=entry.horse_weight,
            weight_change=entry.weight_change,
        ))
    return result


@router.get("/{race_id}/results")
def get_results(race_id: int, db: DbDep) -> list[ResultOut]:
    """レースの成績を返す。"""
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    results = (
        db.query(RaceResult, Horse)
        .join(Horse, RaceResult.horse_id == Horse.id)
        .filter(RaceResult.race_id == race_id)
        .order_by(RaceResult.finish_position.asc().nullslast())
        .all()
    )

    return [
        ResultOut(
            horse_number=r.horse_number,
            finish_position=r.finish_position,
            finish_time=float(r.finish_time) if r.finish_time else None,
            last_3f=float(r.last_3f) if r.last_3f else None,
            horse_name=h.name,
        )
        for r, h in results
    ]
