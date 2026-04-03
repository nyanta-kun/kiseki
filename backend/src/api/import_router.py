"""インポートAPIルーター

Windows Agent からのJV-Linkデータ受信エンドポイント。
X-API-Key ヘッダーで簡易認証を行う。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import settings
from ..db.models import OddsHistory, Race, RacePayout, RaceResult
from ..db.session import get_db
from ..importers import ChangeHandler, OddsImporter, PedigreeImporter, RaceImporter
from .races import _fetch_results_payload
from .ws_manager import manager as ws_manager
from .ws_manager import results_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/import", tags=["import"])
changes_router = APIRouter(prefix="/api/changes", tags=["changes"])


# -------------------------------------------------------------------
# 認証依存関数
# -------------------------------------------------------------------
def verify_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    """X-API-Key ヘッダーを検証する。

    本番環境ではAPIキーが必須。開発環境では未設定時に認証省略。
    """
    if not settings.change_notify_api_key or not settings.change_notify_api_key.strip():
        if settings.api_env == "production":
            logger.error("CHANGE_NOTIFY_API_KEY is not set in production environment")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API key not configured",
            )
        return  # 開発環境では認証省略
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


ApiKeyDep = Annotated[None, Depends(verify_api_key)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


# -------------------------------------------------------------------
# リクエストモデル
# -------------------------------------------------------------------
class JvRecord(BaseModel):
    """JV-Link 1レコードの形式。"""

    rec_id: str
    data: str


class ImportRequest(BaseModel):
    """インポートリクエスト共通形式。"""

    records: list[JvRecord]


class ChangeNotifyRequest(BaseModel):
    """変更通知リクエスト（出走取消・騎手変更）。"""

    change_type: str  # "scratch" | "jockey_change"
    raw_data: str
    detected_at: str  # ISO8601


class WeightRequest(BaseModel):
    """馬体重レコード。SEレコードと同じく race_importer で処理。"""

    date: str
    records: list[JvRecord]


class PayoutEntry(BaseModel):
    """払戻情報1件（parse_hr の payouts リスト要素）。"""

    bet_type: str
    combination: str
    payout: int
    popularity: int | None = None


class HrRecord(BaseModel):
    """HR レコード（払戻情報）のパース結果。"""

    rec_id: str
    race_id: str  # 16文字のレースキー（jravan_race_id）
    race_date: str
    course: str
    race_number: int
    payouts: list[PayoutEntry]


class PayoutsImportRequest(BaseModel):
    """払戻インポートリクエスト。"""

    records: list[HrRecord]


# -------------------------------------------------------------------
# エンドポイント
# -------------------------------------------------------------------
@router.post("/races")
async def import_races(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """RA/SEレコード（レース情報・出馬表・成績）を取り込む。

    Windows Agent の run_daily_fetch / run_setup から呼び出される。
    """
    importer = RaceImporter(db)
    records = [r.model_dump() for r in body.records]
    if records:
        first = records[0]
        logger.debug(
            f"recv: rec_id={first.get('rec_id')!r} data[:20]={first.get('data', '')[:20]!r} total={len(records)}"
        )
    stats = await importer.import_records(records)
    await db.commit()
    logger.info(f"import_races stats: {stats}")

    # 成績が確定したレースをWebSocketでブロードキャスト
    for race_id in stats.get("result_race_ids", []):  # type: ignore[union-attr]
        payload = await _fetch_results_payload(race_id, db)
        if payload:
            await results_manager.broadcast(race_id, payload)  # type: ignore[arg-type]

    return {"ok": True, "stats": stats}


@router.post("/entries")
async def import_entries(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """出馬表レコード（SE）を取り込む。

    /races と同じ処理。Windows Agent が分けて送る場合用。
    """
    importer = RaceImporter(db)  # type: ignore[arg-type]
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    await db.commit()
    logger.info(f"import_entries: {stats}")
    return {"ok": True, "stats": stats}


@router.post("/odds")
async def import_odds(
    body: WeightRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """O1-O8オッズレコードを取り込む。更新後WebSocketでブロードキャスト。"""
    importer = OddsImporter(db)  # type: ignore[arg-type]
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    await db.commit()
    logger.info(f"import_odds: {stats}")

    # 更新されたレースのオッズをWebSocketクライアントへブロードキャスト
    for race_id in stats.get("race_ids", []):
        win: dict[str, float] = {}
        place: dict[str, float] = {}
        for bet_type, target in (("win", win), ("place", place)):
            latest_at_result = await db.execute(
                select(func.max(OddsHistory.fetched_at)).where(
                    OddsHistory.race_id == race_id,
                    OddsHistory.bet_type == bet_type,
                )
            )
            latest_at = latest_at_result.scalar()
            if latest_at is None:
                continue
            rows_result = await db.execute(
                select(OddsHistory).where(
                    OddsHistory.race_id == race_id,
                    OddsHistory.bet_type == bet_type,
                    OddsHistory.fetched_at == latest_at,
                )
            )
            rows = rows_result.scalars().all()
            for row in rows:
                if row.odds is not None:
                    target[row.combination] = float(row.odds)
        await ws_manager.broadcast(race_id, {"win": win, "place": place})

    return {"ok": True, "stats": stats}


@router.post("/weights")
async def import_weights(
    body: WeightRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """馬体重レコード（WE, SEの一部）を取り込む。

    WEレコードはSEと同じ馬情報を持つため RaceImporter で処理。
    """
    importer = RaceImporter(db)  # type: ignore[arg-type]
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    await db.commit()
    return {"ok": True, "stats": stats}


@router.post("/bloodlines")
async def import_bloodlines(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """HN/SKレコード（血統データ）を取り込む。

    Windows Agent の run_setup から呼び出される。
    HN (繁殖馬マスタ) と SK (産駒マスタ) を同一バッチで送信すること。
    HN が先に処理されて in-memory 辞書を構築し、SK の馬名解決に使用する。
    """
    importer = PedigreeImporter(db)  # type: ignore[arg-type]
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    await db.commit()
    logger.info(f"import_bloodlines: {stats}")
    return {"ok": True, "stats": stats}


@router.post("/payouts")
async def import_payouts(
    body: PayoutsImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """HR レコード（払戻情報）を取り込む。

    処理内容:
      1. jravan_race_id で races.id を解決する
      2. race_payouts に upsert（UNIQUE制約で重複排除）
      3. 複勝払戻は race_results.place_odds にも更新する（horse_number で照合）
    """
    from decimal import Decimal

    imported = 0
    skipped = 0

    for hr in body.records:
        # races.id を jravan_race_id で解決
        race_row = await db.execute(
            select(Race.id).where(Race.jravan_race_id == hr.race_id)
        )
        race_db_id: int | None = race_row.scalar()
        if race_db_id is None:
            logger.debug(f"import_payouts: race not found for jravan_race_id={hr.race_id!r}")
            skipped += len(hr.payouts)
            continue

        for entry in hr.payouts:
            # race_payouts に upsert
            stmt = (
                pg_insert(RacePayout)
                .values(
                    race_id=race_db_id,
                    bet_type=entry.bet_type,
                    combination=entry.combination,
                    payout=entry.payout,
                    popularity=entry.popularity,
                )
                .on_conflict_do_update(
                    constraint="uq_race_payouts_race_type_combo",
                    set_={
                        "payout": entry.payout,
                        "popularity": entry.popularity,
                    },
                )
            )
            await db.execute(stmt)
            imported += 1

            # 複勝払戻を race_results.place_odds に反映
            if entry.bet_type == "place" and entry.combination.isdigit():
                horse_number = int(entry.combination)
                # payout は 100円あたり払戻金額（整数）→ 倍率に変換
                place_odds_val = Decimal(str(round(entry.payout / 100, 1)))
                result_row = await db.execute(
                    select(RaceResult).where(
                        RaceResult.race_id == race_db_id,
                        RaceResult.horse_number == horse_number,
                    )
                )
                result = result_row.scalar_one_or_none()
                if result is not None:
                    result.place_odds = place_odds_val

    await db.commit()
    logger.info(f"import_payouts: imported={imported}, skipped={skipped}")
    return {"imported": imported, "skipped": skipped}


@changes_router.post("/notify")
async def notify_change(
    body: ChangeNotifyRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """出走取消・騎手変更通知を受信してDBへ記録する。

    変更検知ルール（CLAUDE.md）:
      scratch      → 該当レース全馬を再算出
      jockey_change → 該当馬の騎手指数 + 全馬の展開指数を再算出
    """
    handler = ChangeHandler(db)  # type: ignore[arg-type]
    result = handler.handle(body.change_type, body.raw_data)
    await db.commit()

    if result.get("recalc_race_id"):
        logger.warning(
            f"Change recorded: type={body.change_type}, "
            f"race_id={result['recalc_race_id']}, "
            f"recalc_triggered=False (pending scheduler)"
        )
        # TODO: MS5でリアルタイム再算出トリガーを実装

    return {"ok": True, "recorded": result.get("recorded", False)}
