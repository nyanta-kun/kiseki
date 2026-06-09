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
    build_hit_tier_recommendations,
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


class ValueCandidate(BaseModel):
    """妙味候補（収支保証なし・注記）。的中重視推奨の副次情報。"""

    horse_number: int
    horse_name: str | None
    win_odds: float | None
    index_rank: int | None
    badges: list[str]
    # 高オッズ穴 複勝＋ワイド軸（2026-06-09・検証 memory: highodds_place_wide_recommendation）
    is_place_axis: bool = False
    """複勝＋ワイド軸の「軸」該当（単勝≥10×composite上位4×place_prob上位2×バッジ）。"""
    wide_partner_horse_number: int | None = None
    """ワイド相手＝モデルcomposite1位（=本命）の馬番。"""
    finish_position: int | None = None
    """確定着順（レース後表示用）。"""


class RecommendationOut(BaseModel):
    """推奨1件のレスポンス。"""

    id: int
    rank: int
    race: RaceInfo
    bet_type: str
    # 統合ランク体系 (bet-structure-guide.md 準拠)
    tier: str | None = None
    """ランク: SS / S / A (単勝実証済み) / 3F-2軸 / 3F-BOX (3連複仮説)。"""
    ticket_combos: list[list[int]] | None = None
    """実際の買い目組み合わせ (単勝: [[馬番]] / 3連複: [[1,2,3],[1,2,4],...]）。"""
    points: int | None = None
    """合計点数。"""
    roi_basis: float | None = None
    """バックテスト実証ROI (None=仮説)。"""
    is_verified: bool | None = None
    """バックテスト実証済みか。"""
    value_candidates: list[ValueCandidate] | None = None
    """妙味候補（穴・収支保証なし）。的中重視推奨の副次情報。"""
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
        tier=c.get("tier"),
        ticket_combos=c.get("ticket_combos"),
        points=c.get("points"),
        roi_basis=c.get("roi_basis"),
        is_verified=c.get("is_verified"),
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


def _hit_tier_to_out(c: dict[str, Any]) -> RecommendationOut:
    """build_hit_tier_recommendations() が返す dict を RecommendationOut に変換。"""
    out = _sweet_spot_to_out(c)
    out.value_candidates = [
        ValueCandidate(
            horse_number=v["horse_number"],
            horse_name=v.get("horse_name"),
            win_odds=v.get("win_odds"),
            index_rank=v.get("index_rank"),
            badges=v.get("badges", []),
            is_place_axis=v.get("is_place_axis", False),
            wide_partner_horse_number=v.get("wide_partner_horse_number"),
            finish_position=v.get("finish_position"),
        )
        for v in (c.get("value_candidates") or [])
    ]
    return out


_HIT_TIER_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_HIT_TIER_LOCKS: dict[str, asyncio.Lock] = {}
_HIT_TIER_TTL_SEC = 60.0


async def _build_hit_tier_cached(db: AsyncSession, date: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _HIT_TIER_CACHE.get(date)
    if cached and now - cached[0] < _HIT_TIER_TTL_SEC:
        return cached[1]
    lock = _HIT_TIER_LOCKS.setdefault(date, asyncio.Lock())
    async with lock:
        cached = _HIT_TIER_CACHE.get(date)
        if cached and time.monotonic() - cached[0] < _HIT_TIER_TTL_SEC:
            return cached[1]
        result = await build_hit_tier_recommendations(db, date)
        _HIT_TIER_CACHE[date] = (time.monotonic(), result)
        return result


@router.get("", response_model=list[RecommendationOut])
async def get_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> list[RecommendationOut]:
    """指定日の的中重視 自動推奨を返す（最新オッズ反映）。

    2026-06-05 再定義: OOS検証で JRA 単一レースの価値(ROI)系バッジは全て脆弱と判明したため、
    推奨エンジンを「1レース1推奨＝指数1位馬 ＋ 信頼度tier(S鉄板/A信頼/B複勝圏)」へ変更。
    混戦(C)は推奨しない。価値系は value_candidates（妙味候補・収支保証なし）に降格。
    tier別1位馬の的中率は OOS で単調(S勝率67%/複勝93% … A34/71 … B26/64)。

    返却順は発走時刻順（post_time 昇順）。プロセス内60秒キャッシュ + フロント60秒 revalidate。
    """
    try:
        candidates = await _build_hit_tier_cached(db, date)
    except Exception as e:
        logger.error("的中tier 推奨生成失敗: %s", e)
        return []
    items = [_hit_tier_to_out(c) for c in candidates]
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
