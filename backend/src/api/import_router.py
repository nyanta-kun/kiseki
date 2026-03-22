"""インポートAPIルーター

Windows Agent からのJV-Linkデータ受信エンドポイント。
X-API-Key ヘッダーで簡易認証を行う。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db.session import get_db
from ..importers import ChangeHandler, OddsImporter, RaceImporter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/import", tags=["import"])
changes_router = APIRouter(prefix="/api/changes", tags=["changes"])


# -------------------------------------------------------------------
# 認証依存関数
# -------------------------------------------------------------------
def verify_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    """X-API-Key ヘッダーを検証する。

    settings.change_notify_api_key が空の場合は開発モードとして認証スキップ。
    """
    if not settings.change_notify_api_key:
        return  # 開発環境では認証省略
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


ApiKeyDep = Annotated[None, Depends(verify_api_key)]
DbDep = Annotated[Session, Depends(get_db)]


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
    # デバッグ: 受信レコードの先頭を確認
    if records:
        first = records[0]
        logger.warning(f"DEBUG recv: rec_id={first.get('rec_id')!r} data[:20]={first.get('data','')[:20]!r} total={len(records)}")
    stats = importer.import_records(records)
    db.commit()
    logger.warning(f"import_races stats: {stats}")
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
    importer = RaceImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    db.commit()
    logger.info(f"import_entries: {stats}")
    return {"ok": True, "stats": stats}


@router.post("/odds")
async def import_odds(
    body: WeightRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """O1-O8オッズレコードを取り込む。"""
    importer = OddsImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    db.commit()
    logger.info(f"import_odds: {stats}")
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
    importer = RaceImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    db.commit()
    return {"ok": True, "stats": stats}


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
    handler = ChangeHandler(db)
    result = handler.handle(body.change_type, body.raw_data)
    db.commit()

    if result.get("recalc_race_id"):
        logger.warning(
            f"Change recorded: type={body.change_type}, "
            f"race_id={result['recalc_race_id']}, "
            f"recalc_triggered=False (pending scheduler)"
        )
        # TODO: MS5でリアルタイム再算出トリガーを実装

    return {"ok": True, "recorded": result.get("recorded", False)}
