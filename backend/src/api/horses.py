"""馬情報APIルーター

馬ごとの近走成績・指数推移を返すエンドポイント。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CalculatedIndex, Horse, NetkeibaRaceExtra, ProvisionalHorse, Race, RaceResult
from ..db.session import get_db
from ..indices.composite import COMPOSITE_VERSION

router = APIRouter(prefix="/api/horses", tags=["horses"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


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


@router.get("/known-ids")
async def get_known_horse_ids(
    birth_year: int = Query(description="生産年（例: 2024）"),
    db: DbDep = None,  # type: ignore[assignment]
) -> dict:
    """keiba.horses と provisional_horses の両方で既知の netkeiba horse_id セットを返す。

    scrape_netkeiba_2yo.py が一覧走査の早期終了判定に使用する。
    horse_id 先頭4桁が生産年と一致するものを対象とする。
    """
    prefix = str(birth_year)

    # keiba.horses: jravan_code = netkeiba horse_id と同一形式
    jravan_rows = await db.execute(
        select(Horse.jravan_code).where(
            Horse.jravan_code.like(f"{prefix}%")
        )
    )
    ids: set[str] = {r.jravan_code for r in jravan_rows if r.jravan_code}

    # provisional_horses: netkeiba_horse_id
    prov_rows = await db.execute(
        select(ProvisionalHorse.netkeiba_horse_id).where(
            ProvisionalHorse.netkeiba_horse_id.like(f"{prefix}%")
        )
    )
    ids.update(r.netkeiba_horse_id for r in prov_rows)

    return {"birth_year": birth_year, "ids": sorted(ids)}


@router.get("/provisional")
async def list_provisional_horses(
    birth_year: int | None = Query(None, description="生産年フィルタ"),
    unmerged_only: bool = Query(True, description="未マージのみ返す"),
    db: DbDep = None,  # type: ignore[assignment]
) -> list[dict]:
    """暫定馬マスタ一覧を返す（管理・確認用）。"""
    stmt = select(ProvisionalHorse)
    if birth_year:
        stmt = stmt.where(ProvisionalHorse.birth_year == birth_year)
    if unmerged_only:
        stmt = stmt.where(ProvisionalHorse.merged_horse_id.is_(None))
    stmt = stmt.order_by(ProvisionalHorse.name)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "netkeiba_horse_id": r.netkeiba_horse_id,
            "name": r.name,
            "birth_year": r.birth_year,
            "birth_date": r.birth_date,
            "sex": r.sex,
            "sire_name": r.sire_name,
            "dam_name": r.dam_name,
            "trainer_name": r.trainer_name,
            "owner_name": r.owner_name,
            "farm_name": r.farm_name,
            "merged_horse_id": r.merged_horse_id,
            "merged_at": r.merged_at.isoformat() if r.merged_at else None,
        }
        for r in rows
    ]


@router.get("/{horse_id}/history")
async def get_horse_history(horse_id: int, db: DbDep) -> list[RaceHistoryEntry]:
    """馬の近走成績と指数推移を返す（最新5走）。"""
    horse_result = await db.execute(select(Horse).where(Horse.id == horse_id))
    horse = horse_result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Horse not found")

    stmt = (
        select(RaceResult, Race)
        .join(Race, RaceResult.race_id == Race.id)
        .where(RaceResult.horse_id == horse_id)
        .where(RaceResult.finish_position.isnot(None))
        .order_by(Race.date.desc())
        .limit(5)
    )
    rows_result = await db.execute(stmt)
    rows = rows_result.all()

    race_ids = [race.id for _, race in rows]
    indices: dict[int, CalculatedIndex] = {}
    extras: dict[int, NetkeibaRaceExtra] = {}
    if race_ids:
        ci_result = await db.execute(
            select(CalculatedIndex).where(
                CalculatedIndex.race_id.in_(race_ids),
                CalculatedIndex.horse_id == horse_id,
                CalculatedIndex.version == COMPOSITE_VERSION,
            )
        )
        ci_rows = ci_result.scalars().all()
        indices = {ci.race_id: ci for ci in ci_rows}

        extra_result = await db.execute(
            select(NetkeibaRaceExtra).where(
                NetkeibaRaceExtra.race_id.in_(race_ids),
                NetkeibaRaceExtra.horse_id == horse_id,
            )
        )
        extra_rows = extra_result.scalars().all()
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
            composite_index=float(indices[race.id].composite_index) if race.id in indices and indices[race.id].composite_index is not None else None,  # type: ignore[arg-type]
            remarks=extras[race.id].remarks if race.id in extras else None,
        )
        for rr, race in rows
    ]
