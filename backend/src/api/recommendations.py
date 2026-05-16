"""推奨レース・馬券APIルーター

GET  /api/recommendations?date=YYYYMMDD
  → DBに保存済みの推奨を返す。未生成の場合は空リスト。

GET  /api/recommendations/source?date=YYYYMMDD
  → Claude定期エージェントが推奨選定に使うソースデータを返す（X-API-Key認証）。

POST /api/recommendations/submit?date=YYYYMMDD
  → Claude定期エージェントが選定した推奨5件を保存（X-API-Key認証）。

POST /api/recommendations/update-results?date=YYYYMMDD
  → 結果確定後の的中・払戻更新。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import Race, RaceRecommendation
from ..db.session import get_db
from ..services.recommender import (
    build_anagusa_rule_recommendations,
    build_sweet_spot_recommendations,
    collect_recommendation_source,
    submit_recommendations,
    update_results,
)

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


class SubmitItem(BaseModel):
    """Claude定期エージェントが提出する推奨1件。"""

    rank: int
    race_id: int
    bet_type: str  # "win" | "place" | "quinella"
    target_horse_numbers: list[int]
    reason: str
    confidence: float


class SubmitRequest(BaseModel):
    """提出ペイロード。"""

    recommendations: list[SubmitItem]


def _sweet_spot_to_out(c: dict[str, Any]) -> RecommendationOut:
    """build_sweet_spot_recommendations() が返す dict を RecommendationOut に変換。"""
    race_info = RaceInfo(
        race_id=c["race_id"],
        course_name=c["course_name"],
        race_number=c["race_number"],
        race_name=c.get("race_name"),
        post_time=c.get("post_time"),
        surface=c.get("surface"),
        distance=c.get("distance"),
        grade=c.get("grade"),
        head_count=c.get("head_count"),
    )
    target = [
        TargetHorse(
            horse_number=h["horse_number"],
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
        for h in c["target_horses"]
    ]
    return RecommendationOut(
        id=c["id"],
        rank=c["rank"],
        race=race_info,
        bet_type=c["bet_type"],
        target_horses=target,
        snapshot_win_odds=c.get("snapshot_win_odds"),
        snapshot_place_odds=c.get("snapshot_place_odds"),
        snapshot_at=c.get("snapshot_at"),
        reason=c["reason"],
        confidence=c["confidence"],
        result_correct=c.get("result_correct"),
        result_payout=c.get("result_payout"),
        result_updated_at=c.get("result_updated_at"),
        created_at=c["created_at"],
    )


_SWEET_SPOT_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SWEET_SPOT_LOCKS: dict[str, asyncio.Lock] = {}
_SWEET_SPOT_TTL_SEC = 60.0


async def _build_sweet_spot_cached(
    db: AsyncSession, date: str
) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _SWEET_SPOT_CACHE.get(date)
    if cached and now - cached[0] < _SWEET_SPOT_TTL_SEC:
        return cached[1]
    lock = _SWEET_SPOT_LOCKS.setdefault(date, asyncio.Lock())
    async with lock:
        cached = _SWEET_SPOT_CACHE.get(date)
        if cached and time.monotonic() - cached[0] < _SWEET_SPOT_TTL_SEC:
            return cached[1]
        result = await build_sweet_spot_recommendations(db, date)
        _SWEET_SPOT_CACHE[date] = (time.monotonic(), result)
        return result


@router.get("", response_model=list[RecommendationOut])
async def get_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> list[RecommendationOut]:
    """指定日のスイートスポット自動推奨を返す（最新オッズ反映）。

    抽出条件: 単勝≥10 ∧ 期待値 1.2-5.0 ∧ バッジあり ∧ レース内 k≤2。
    呼び出しごとに最新オッズで再判定するため、出走直前まで反映される。
    Claude.ai Routine の AI 推奨は使わない（DBの race_recommendations は無視）。

    返却順は発走時刻順（post_time 昇順）。rank は最大EV降順で連番。
    3年バックテスト実証: 単ROI 1.188 / 複ROI 0.826。

    プロセス内 60 秒メモリキャッシュ + フロント 60 秒 revalidate を併用。
    """
    try:
        candidates = await _build_sweet_spot_cached(db, date)
    except Exception as e:
        logger.error("sweet spot 推奨生成失敗: %s", e)
        return []
    items = [_sweet_spot_to_out(c) for c in candidates]
    items.sort(key=lambda x: (x.race.post_time is None, x.race.post_time or "", x.rank))
    return items


class AnagusaRuleItem(BaseModel):
    """穴ぐさルール推奨1馬券のレスポンス。"""

    rule_label: str
    rule_desc: str
    bet_type: str  # "place" | "win_place"
    race_id: int
    course_name: str
    race_number: int
    race_name: str | None
    post_time: str | None
    distance: int
    surface: str
    horse_number: int
    horse_name: str | None
    win_odds: float | None
    place_odds: float | None
    popularity: int | None
    is_preferred_pop: bool  # 人気4-6（最優先条件）
    finish_position: int | None
    backtest_place_roi: float
    backtest_win_roi: float | None
    backtest_n: int
    snapshot_at: datetime | None


_ANAGUSA_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ANAGUSA_LOCKS: dict[str, asyncio.Lock] = {}
_ANAGUSA_TTL_SEC = 60.0


async def _build_anagusa_cached(db: AsyncSession, date: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _ANAGUSA_CACHE.get(date)
    if cached and now - cached[0] < _ANAGUSA_TTL_SEC:
        return cached[1]
    lock = _ANAGUSA_LOCKS.setdefault(date, asyncio.Lock())
    async with lock:
        cached = _ANAGUSA_CACHE.get(date)
        if cached and time.monotonic() - cached[0] < _ANAGUSA_TTL_SEC:
            return cached[1]
        result = await build_anagusa_rule_recommendations(db, date)
        _ANAGUSA_CACHE[date] = (time.monotonic(), result)
        return result


@router.get("/anagusa-rules", response_model=list[AnagusaRuleItem])
async def get_anagusa_rule_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> list[AnagusaRuleItem]:
    """穴ぐさ条件ルールに基づく推奨馬を返す（都度算出）。

    Rule1: 東京×芝×1201-1800m × rank_A → 複勝
    Rule2: 新潟×芝×1601-1800m × rank_A → 単+複
    Rule3: 京都×芝×~1200m × rank_A → 単+複
    Rule4: 京都×ダ×1601-1800m × rank_A → 単+複
    人気4-6が最優先（is_preferred_pop=true）。
    3年バックテスト複ROI: 1.030〜1.168。
    """
    try:
        items = await _build_anagusa_cached(db, date)
    except Exception as e:
        logger.error("穴ぐさルール推奨生成失敗: %s", e)
        return []
    return [AnagusaRuleItem(**item) for item in items]


@router.get("/source")
async def get_recommendation_source(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict[str, Any]:
    """Claude定期エージェントが推奨選定に使うソースデータを返す。

    指数・オッズ・外部指数（netkeiba/kichiuma）を含むレースリスト。
    races_with_odds=0 の場合はエージェントは推奨生成をスキップする。
    """
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await collect_recommendation_source(db, date)


@router.post("/submit", response_model=list[RecommendationOut])
async def submit_recommendation(
    db: DbDep,
    payload: SubmitRequest,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> list[RecommendationOut]:
    """Claude定期エージェントが選定した推奨をDBに保存する。

    既存レコードを削除して上書き。ハードフィルター・体言止め変換は
    submit_recommendations() 内で適用される。
    """
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = [item.model_dump() for item in payload.recommendations]
    try:
        recs = await submit_recommendations(db, date, items)
    except Exception as e:
        logger.error("推奨提出失敗: %s", e)
        raise HTTPException(status_code=500, detail=f"推奨保存に失敗しました: {e}")

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
