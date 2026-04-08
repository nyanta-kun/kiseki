"""地方競馬推奨レース・馬券APIルーター

GET  /api/chihou/recommendations?date=YYYYMMDD
POST /api/chihou/recommendations/generate        (X-API-Key認証)
POST /api/chihou/recommendations/update-results  (X-API-Key認証)
POST /api/chihou/recommendations/update-odds-decision  (X-API-Key認証)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.chihou_models import ChihouRace, ChihouRaceRecommendation
from ..db.session import get_db
from ..services.chihou_recommender import (
    generate_chihou_recommendations,
    update_chihou_odds_decision,
    update_chihou_results,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chihou/recommendations", tags=["chihou-recommendations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# レスポンス型
# ---------------------------------------------------------------------------


class ChihouTargetHorse(BaseModel):
    """推奨馬情報。"""

    horse_number: int
    horse_name: str | None
    composite_index: float | None
    win_probability: float | None
    place_probability: float | None
    finish_position: int | None = None


class ChihouRaceInfo(BaseModel):
    """レース概要情報。"""

    race_id: int
    course_name: str
    race_number: int
    race_name: str | None
    post_time: str | None
    surface: str | None
    distance: int | None
    head_count: int | None


class ChihouRecommendationOut(BaseModel):
    """地方競馬推奨レスポンス。"""

    id: int
    rank: int
    race: ChihouRaceInfo
    bet_type: str
    target_horses: list[ChihouTargetHorse]
    reason: str
    confidence: float
    # 10分前オッズ判断
    odds_decision: str | None  # "buy" | "pass" | None
    odds_decision_at: datetime | None
    odds_decision_reason: str | None
    snapshot_win_odds: dict[str, float] | None
    snapshot_place_odds: dict[str, float] | None
    snapshot_at: datetime | None
    # 結果
    result_correct: bool | None
    result_payout: int | None
    result_updated_at: datetime | None
    created_at: datetime


def _to_out(rec: ChihouRaceRecommendation, race: ChihouRace) -> ChihouRecommendationOut:
    """DB モデルをレスポンス型に変換する。"""
    race_info = ChihouRaceInfo(
        race_id=race.id,
        course_name=race.course_name,
        race_number=race.race_number,
        race_name=race.race_name,
        post_time=race.post_time,
        surface=race.surface,
        distance=race.distance,
        head_count=None,
    )
    target: list[ChihouTargetHorse] = [
        ChihouTargetHorse(
            horse_number=h.get("horse_number", 0),
            horse_name=h.get("horse_name"),
            composite_index=h.get("composite_index"),
            win_probability=h.get("win_probability"),
            place_probability=h.get("place_probability"),
            finish_position=h.get("finish_position"),
        )
        for h in (rec.target_horses or [])
    ]
    return ChihouRecommendationOut(
        id=rec.id,
        rank=rec.rank,
        race=race_info,
        bet_type=rec.bet_type,
        target_horses=target,
        reason=rec.reason,
        confidence=rec.confidence,
        odds_decision=rec.odds_decision,
        odds_decision_at=rec.odds_decision_at,
        odds_decision_reason=rec.odds_decision_reason,
        snapshot_win_odds=rec.snapshot_win_odds,
        snapshot_place_odds=rec.snapshot_place_odds,
        snapshot_at=rec.snapshot_at,
        result_correct=rec.result_correct,
        result_payout=rec.result_payout,
        result_updated_at=rec.result_updated_at,
        created_at=rec.created_at,
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ChihouRecommendationOut])
async def get_chihou_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> list[ChihouRecommendationOut]:
    """指定日の地方競馬推奨を返す。未生成の場合はオンデマンド生成。"""
    result = await db.execute(
        select(ChihouRaceRecommendation)
        .where(ChihouRaceRecommendation.date == date)
        .order_by(ChihouRaceRecommendation.rank)
    )
    recs = result.scalars().all()

    if not recs:
        try:
            recs = await generate_chihou_recommendations(db, date)
        except Exception as e:
            logger.error("地方推奨オンデマンド生成失敗: %s", e)
            # API過負荷・一時エラー時は空リストを返す（503でクライアントを壊さない）
            return []

    if not recs:
        return []

    race_ids = [rec.race_id for rec in recs]
    races_result = await db.execute(select(ChihouRace).where(ChihouRace.id.in_(race_ids)))
    races_map = {r.id: r for r in races_result.scalars().all()}

    return [_to_out(rec, races_map[rec.race_id]) for rec in recs if rec.race_id in races_map]


@router.post("/generate", response_model=list[ChihouRecommendationOut])
async def force_generate_chihou(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> list[ChihouRecommendationOut]:
    """地方推奨を強制再生成（管理用）。"""
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        recs = await generate_chihou_recommendations(db, date)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"推奨生成に失敗しました: {e}")
    if not recs:
        return []
    race_ids = [rec.race_id for rec in recs]
    races_result = await db.execute(select(ChihouRace).where(ChihouRace.id.in_(race_ids)))
    races_map = {r.id: r for r in races_result.scalars().all()}
    return [_to_out(rec, races_map[rec.race_id]) for rec in recs if rec.race_id in races_map]


@router.post("/update-results")
async def update_chihou_recommendation_results(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict[str, Any]:
    """地方推奨の的中・払戻を更新（レース後）。"""
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    count = await update_chihou_results(db, date)
    return {"updated": count, "date": date}


@router.post("/update-odds-decision")
async def update_chihou_odds_decision_endpoint(
    db: DbDep,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict[str, Any]:
    """発走10分前のオッズからbuy/pass判断を更新（毎分cron）。"""
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    count = await update_chihou_odds_decision(db)
    return {"updated": count}
