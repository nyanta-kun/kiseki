"""地方競馬推奨レース・馬券APIルーター

GET  /api/chihou/recommendations?date=YYYYMMDD
GET  /api/chihou/recommendations/source              (X-API-Key認証)
POST /api/chihou/recommendations/submit              (X-API-Key認証)
POST /api/chihou/recommendations/update-results      (X-API-Key認証)
POST /api/chihou/recommendations/update-odds-decision  (X-API-Key認証)
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.chihou_models import ChihouRace, ChihouRaceRecommendation
from ..db.session import get_db
from ..services.chihou_recommender import (
    build_chihou_sweet_spot_recommendations,
    collect_chihou_recommendation_source,
    submit_chihou_recommendations,
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
    external_consensus: int | None = None  # 0〜2: kichiuma/netkeibaで1位になった数
    win_odds: float | None = None
    place_odds: float | None = None
    ev: float | None = None  # win_probability × win_odds


class RaceConcentration(BaseModel):
    """レース内の複勝確率集中度。

    top2_share > 0.873 → high (1位複勝ヒット率 76.5%)
    top2_share ≤ 0.715 → low  (1位複勝ヒット率 57.0%)
    """

    top2_share: float | None       # 上位2頭の複勝確率シェア
    hhi: float | None              # ハーフィンダール指数
    confidence_level: str | None   # "high" | "medium" | "low"


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
    # カテゴリ: "sweet_spot"=高オッズ穴 / "low_odds_trusted"=信頼本命 /
    #           "low_odds_untrusted"=不信頼本命 / null=既存DB保存推奨
    category: str | None = None
    target_horses: list[ChihouTargetHorse]
    reason: str
    confidence: float
    race_concentration: RaceConcentration | None = Field(...)  # Required: FastAPIのexclude_defaultsで除外させない
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


class ChihouCategorySummary(BaseModel):
    """カテゴリ単位の当日確定済み集計。"""

    n_total: int           # カテゴリ抽出された推奨数
    n_settled: int         # 結果確定済みの件数
    n_hits: int            # 的中件数（win=1着, place=1〜3着）
    hit_rate: float | None # n_settled > 0 の時のみ
    win_roi: float | None  # ROI（bet_type に応じ単勝/複勝）。n_settled > 0 の時のみ
    bet_type: str | None = None  # "win" | "place" — フロント側のラベル表示用


class ChihouSweetSpotResponse(BaseModel):
    """スイートスポット推奨 + カテゴリ別 当日合計集計のレスポンス。"""

    items: list[ChihouRecommendationOut]
    summaries: dict[str, ChihouCategorySummary]


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
            external_consensus=h.get("external_consensus"),
        )
        for h in (rec.target_horses or [])
    ]
    return ChihouRecommendationOut(
        id=rec.id,
        rank=rec.rank,
        race=race_info,
        bet_type=rec.bet_type,
        race_concentration=None,
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


def _chihou_sweet_spot_to_out(c: dict[str, Any]) -> ChihouRecommendationOut:
    """build_chihou_sweet_spot_recommendations() の dict → ChihouRecommendationOut 変換。"""
    race_info = ChihouRaceInfo(
        race_id=c["race_id"],
        course_name=c["course_name"],
        race_number=c["race_number"],
        race_name=c.get("race_name"),
        post_time=c.get("post_time"),
        surface=c.get("surface"),
        distance=c.get("distance"),
        head_count=c.get("head_count"),
    )
    target = [
        ChihouTargetHorse(
            horse_number=h["horse_number"],
            horse_name=h.get("horse_name"),
            composite_index=h.get("composite_index"),
            win_probability=h.get("win_probability"),
            place_probability=h.get("place_probability"),
            finish_position=h.get("finish_position"),
            win_odds=h.get("win_odds"),
            place_odds=h.get("place_odds"),
            ev=h.get("ev"),
        )
        for h in c["target_horses"]
    ]
    raw_conc = c.get("race_concentration")
    concentration = (
        RaceConcentration(
            top2_share=raw_conc.get("top2_share"),
            hhi=raw_conc.get("hhi"),
            confidence_level=raw_conc.get("confidence_level"),
        )
        if raw_conc
        else None
    )
    return ChihouRecommendationOut(
        id=c["id"],
        rank=c["rank"],
        race=race_info,
        bet_type=c["bet_type"],
        category=c.get("category"),
        race_concentration=concentration,
        target_horses=target,
        reason=c["reason"],
        confidence=c["confidence"],
        odds_decision=None,
        odds_decision_at=None,
        odds_decision_reason=None,
        snapshot_win_odds=c.get("snapshot_win_odds"),
        snapshot_place_odds=c.get("snapshot_place_odds"),
        snapshot_at=c.get("snapshot_at"),
        result_correct=c.get("result_correct"),
        result_payout=c.get("result_payout"),
        result_updated_at=c.get("result_updated_at"),
        created_at=c["created_at"],
    )


def _summarize_by_category(
    items: list[ChihouRecommendationOut],
) -> dict[str, ChihouCategorySummary]:
    """カテゴリ単位で当日合計集計を作る。

    n_settled: result_updated_at が None でない件数（=結果確定済み）。
    n_hits: result_correct == True の件数。
    win_roi: 払戻総額 / (n_settled × 100円)。bet_type=="win" のみ。
    """
    grouped: dict[str, list[ChihouRecommendationOut]] = {}
    for it in items:
        if not it.category:
            continue
        grouped.setdefault(it.category, []).append(it)

    out: dict[str, ChihouCategorySummary] = {}
    for category, recs in grouped.items():
        n_total = len(recs)
        settled = [r for r in recs if r.result_updated_at is not None]
        n_settled = len(settled)
        n_hits = sum(1 for r in settled if r.result_correct is True)
        hit_rate = (n_hits / n_settled) if n_settled else None
        bet_type = recs[0].bet_type if recs else None
        # bet_type が混在する場合はフィールドを None にする（混乱回避）
        if any(r.bet_type != bet_type for r in recs):
            bet_type = None
        if n_settled and bet_type in ("win", "place"):
            # result_payout が None（払戻オッズ未取得）の的中は ROI 計算から除外
            payouts = [r.result_payout for r in settled if r.result_payout is not None]
            roi = sum(payouts) / (len(payouts) * 100) if payouts else None
        else:
            roi = None
        out[category] = ChihouCategorySummary(
            n_total=n_total,
            n_settled=n_settled,
            n_hits=n_hits,
            hit_rate=hit_rate,
            win_roi=roi,
            bet_type=bet_type,
        )
    return out


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


class ChihouSubmitItem(BaseModel):
    """Claude定期エージェントが提出する地方推奨1件。"""

    rank: int
    race_id: int
    bet_type: str  # "win" | "place"
    target_horse_numbers: list[int]
    reason: str
    confidence: float


class ChihouSubmitRequest(BaseModel):
    """提出ペイロード。"""

    recommendations: list[ChihouSubmitItem]


@router.get("", response_model=list[ChihouRecommendationOut])
async def get_chihou_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> list[ChihouRecommendationOut]:
    """指定日の地方競馬推奨を返す（DB保存済みのもの）。

    返却順は発走時刻順（post_time 昇順）。rank フィールドは推奨度順のまま保持。
    Claude定期エージェントが未提出の場合は空リストを返す。
    """
    result = await db.execute(
        select(ChihouRaceRecommendation).where(ChihouRaceRecommendation.date == date)
    )
    recs = result.scalars().all()
    if not recs:
        return []

    race_ids = [rec.race_id for rec in recs]
    races_result = await db.execute(select(ChihouRace).where(ChihouRace.id.in_(race_ids)))
    races_map = {r.id: r for r in races_result.scalars().all()}

    items = [_to_out(rec, races_map[rec.race_id]) for rec in recs if rec.race_id in races_map]
    # 発走時刻順にソート（post_time が None は末尾）
    items.sort(key=lambda x: (x.race.post_time is None, x.race.post_time or "", x.rank))
    return items


@router.get("/source")
async def get_chihou_recommendation_source(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict[str, Any]:
    """Claude定期エージェントが地方推奨選定に使うソースデータを返す。

    オッズなし（指数・外部指数コンセンサスのみ）。
    races_total=0 の場合エージェントは推奨生成をスキップする。
    """
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await collect_chihou_recommendation_source(db, date)


@router.post("/submit", response_model=list[ChihouRecommendationOut])
async def submit_chihou_recommendation(
    db: DbDep,
    payload: ChihouSubmitRequest,
    date: str = Query(..., description="開催日 YYYYMMDD"),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> list[ChihouRecommendationOut]:
    """Claude定期エージェントが選定した地方推奨をDBに保存する。"""
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    items = [item.model_dump() for item in payload.recommendations]
    try:
        recs = await submit_chihou_recommendations(db, date, items)
    except Exception as e:
        logger.error("地方推奨提出失敗: %s", e)
        raise HTTPException(status_code=500, detail=f"推奨保存に失敗しました: {e}")
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


_CHIHOU_SWEET_SPOT_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CHIHOU_SWEET_SPOT_LOCKS: dict[str, asyncio.Lock] = {}
_CHIHOU_SWEET_SPOT_TTL_SEC = 30.0


async def _build_chihou_sweet_spot_cached(
    db: AsyncSession, date: str
) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _CHIHOU_SWEET_SPOT_CACHE.get(date)
    if cached and now - cached[0] < _CHIHOU_SWEET_SPOT_TTL_SEC:
        return cached[1]
    lock = _CHIHOU_SWEET_SPOT_LOCKS.setdefault(date, asyncio.Lock())
    async with lock:
        cached = _CHIHOU_SWEET_SPOT_CACHE.get(date)
        if cached and time.monotonic() - cached[0] < _CHIHOU_SWEET_SPOT_TTL_SEC:
            return cached[1]
        result = await build_chihou_sweet_spot_recommendations(db, date)
        _CHIHOU_SWEET_SPOT_CACHE[date] = (time.monotonic(), result)
        return result


@router.get("/sweet-spot")
async def get_chihou_sweet_spot_recommendations(
    db: DbDep,
    date: str = Query(..., description="開催日 YYYYMMDD"),
) -> JSONResponse:
    """地方競馬スイートスポット自動推奨を返す（最新オッズ反映・都度算出）。

    返却内容:
      - items: 全カテゴリの推奨を並べたリスト（category フィールドで識別）
        * sweet_spot         — 高オッズ穴狙い (単勝≥10 ∧ EV 1.0-2.0 ∧ ROI陽性9場 ∧ k≤2)
        * low_odds_trusted   — 信頼できる本命 (単勝<1.5)
        * low_odds_untrusted — 信頼できない本命 (1.5≤単勝<2.0)
      - summaries: カテゴリ別の当日確定済み件数・的中数・的中率・単勝ROI

    プロセス内 60 秒メモリキャッシュ + フロント 60 秒 revalidate を併用。
    JSONResponse で直接返すことで Pydantic の exclude_defaults を回避し
    race_concentration フィールドが null でも確実に含まれる。
    """
    try:
        candidates = await _build_chihou_sweet_spot_cached(db, date)
    except Exception as e:
        logger.error("地方スイートスポット生成失敗: %s", e)
        return JSONResponse(content={"items": [], "summaries": {}})
    items = [_chihou_sweet_spot_to_out(c) for c in candidates]
    items.sort(key=lambda x: (x.race.post_time is None, x.race.post_time or "", x.rank))
    summaries = _summarize_by_category(items)
    response = ChihouSweetSpotResponse(items=items, summaries=summaries)
    dumped = response.model_dump(mode="json")
    return JSONResponse(content=dumped)
