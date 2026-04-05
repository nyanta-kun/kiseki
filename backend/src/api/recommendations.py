"""推奨レース・馬券APIルーター

GET /api/recommendations?date=YYYYMMDD
  → Claude APIで生成した推奨を返す。DBに保存済みなら即返し、未生成なら生成してから返す。

POST /api/recommendations/generate?date=YYYYMMDD
  → 強制再生成（管理用）。X-API-Keyによる認証必須。

POST /api/recommendations/update-results?date=YYYYMMDD
  → 結果確定後の的中・払戻更新（daily_fetch.shから呼ばれる想定）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import Race, RaceRecommendation
from ..db.session import get_db
from ..services.recommender import generate_recommendations, update_results

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# レスポンス型
# ---------------------------------------------------------------------------


class TargetHorse(BaseModel):
    """推奨馬の情報（スナップショット時点）。"""

    horse_number: int
    horse_name: str | None
    composite_index: float | None
    win_probability: float | None
    place_probability: float | None
    ev_win: float | None
    ev_place: float | None
    win_odds: float | None
    place_odds: float | None
    finish_position: int | None = None  # 結果更新後に追記


class RaceInfo(BaseModel):
    """推奨レースのレース情報。"""

    race_id: int
    course_name: str
    race_number: int
    race_name: str | None
    post_time: str | None
    surface: str | None
    distance: int | None
    grade: str | None
    head_count: int | None


class RecommendationOut(BaseModel):
    """推奨1件のレスポンス。"""

    id: int
    rank: int
    race: RaceInfo
    bet_type: str
    target_horses: list[TargetHorse]
    snapshot_win_odds: dict[str, float] | None
    snapshot_place_odds: dict[str, float] | None
    snapshot_at: datetime | None
    reason: str
    confidence: float
    # 結果（レース後に更新）
    result_correct: bool | None
    result_payout: int | None
    result_updated_at: datetime | None
    created_at: datetime


def _to_out(rec: RaceRecommendation, race: Race) -> RecommendationOut:
    """DBモデル → レスポンスモデル変換。"""
    race_info = RaceInfo(
        race_id=race.id,
        course_name=race.course_name,
        race_number=race.race_number,
        race_name=race.race_name,
        post_time=race.post_time,
        surface=race.surface,
        distance=race.distance,
        grade=race.grade,
        head_count=race.head_count,
    )
    target: list[TargetHorse] = [
        TargetHorse(
            horse_number=h.get("horse_number", 0),
            horse_name=h.get("horse_name"),
            composite_index=h.get("composite_index"),
            win_probability=h.get("win_probability"),
            place_probability=h.get("place_probability"),
            ev_win=h.get("ev_win"),
            ev_place=h.get("ev_place"),
            win_odds=h.get("win_odds"),
            place_odds=h.get("place_odds"),
            finish_position=h.get("finish_position"),
        )
        for h in (rec.target_horses or [])
    ]
    return RecommendationOut(
        id=rec.id,
        rank=rec.rank,
        race=race_info,
        bet_type=rec.bet_type,
        target_horses=target,
        snapshot_win_odds=rec.snapshot_win_odds,
        snapshot_place_odds=rec.snapshot_place_odds,
        snapshot_at=rec.snapshot_at,
        reason=rec.reason,
        confidence=rec.confidence,
        result_correct=rec.result_correct,
        result_payout=rec.result_payout,
        result_updated_at=rec.result_updated_at,
        created_at=rec.created_at,
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("", response_model=list[RecommendationOut])
async def get_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> list[RecommendationOut]:
    """指定日の推奨レース・馬券を返す。

    DBに保存済みであれば即返し。未生成の場合はClaude APIで生成してから返す。
    """
    result = await db.execute(
        select(RaceRecommendation)
        .where(RaceRecommendation.date == date)
        .order_by(RaceRecommendation.rank)
    )
    recs = result.scalars().all()

    if not recs:
        # 未生成 → オンデマンド生成
        try:
            recs = await generate_recommendations(db, date)
        except Exception as e:
            logger.error("推奨オンデマンド生成失敗: %s", e)
            raise HTTPException(status_code=503, detail=f"推奨生成に失敗しました: {e}")

    if not recs:
        return []

    # レース情報を取得
    race_ids = [rec.race_id for rec in recs]
    races_result = await db.execute(select(Race).where(Race.id.in_(race_ids)))
    races_map: dict[int, Race] = {r.id: r for r in races_result.scalars().all()}

    return [_to_out(rec, races_map[rec.race_id]) for rec in recs if rec.race_id in races_map]


@router.post("/generate", response_model=list[RecommendationOut])
async def force_generate(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> list[RecommendationOut]:
    """推奨を強制再生成する（管理用）。"""
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        recs = await generate_recommendations(db, date)
    except Exception as e:
        logger.error("推奨強制生成失敗: %s", e)
        raise HTTPException(status_code=503, detail=f"推奨生成に失敗しました: {e}")

    if not recs:
        return []

    race_ids = [rec.race_id for rec in recs]
    races_result = await db.execute(select(Race).where(Race.id.in_(race_ids)))
    races_map: dict[int, Race] = {r.id: r for r in races_result.scalars().all()}

    return [_to_out(rec, races_map[rec.race_id]) for rec in recs if rec.race_id in races_map]


@router.post("/update-results")
async def update_recommendation_results(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict[str, Any]:
    """指定日の推奨の的中・払戻を成績データから更新する。"""
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    count = await update_results(db, date)
    return {"updated": count, "date": date}
