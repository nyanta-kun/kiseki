"""地方競馬インポートAPIルーター

UmaConn エージェントからの地方競馬データ受信エンドポイント。
X-API-Key ヘッダーで簡易認証を行う。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy import tuple_ as sa_tuple
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import ChihouRace, ChihouRacePayout, ChihouRaceResult
from ..db.session import AsyncSessionLocal, get_db
from ..importers.chihou_odds_importer import ChihouOddsImporter
from ..importers.chihou_pedigree_importer import ChihouPedigreeImporter
from ..importers.chihou_race_importer import ChihouRaceImporter
from ..importers.jvlink_parser import parse_hr
from ..indices.chihou_calculator import BANEI_COURSE_CODE, ChihouIndexCalculator
from .chihou_races_router import _fetch_chihou_results_payload
from .import_router import (
    ImportRequest,
    WeightRequest,
    verify_api_key,
)
from ..services.chihou_recommender import update_chihou_results
from .ws_manager import chihou_results_manager

logger = logging.getLogger(__name__)

chihou_router = APIRouter(prefix="/api/import/chihou", tags=["chihou-import"])

ApiKeyDep = Annotated[None, Depends(verify_api_key)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


async def _fill_loser_place_odds_from_history(
    db: AsyncSession, race_ids: list[int]
) -> int:
    """対象レースの race_results.place_odds NULL 行を odds_history で補完する。

    HR 払戻は 1〜3着馬のみで、4着以下は永続化されない。本処理は同一バッチで
    取り込んだレースの着外馬についても、odds_history の発走前最終 place オッズで
    place_odds を埋める。

    Returns:
        補完した行数。
    """
    if not race_ids:
        return 0

    # race_id × horse_number の最新 place オッズを取得し、NULL 行を一括 UPDATE する。
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (oh.race_id, oh.combination)
                oh.race_id,
                oh.combination AS horse_no_str,
                oh.odds
            FROM chihou.odds_history oh
            WHERE oh.race_id = ANY(:race_ids)
              AND oh.bet_type = 'place'
              AND oh.odds IS NOT NULL
            ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC
        )
        UPDATE chihou.race_results AS rr
        SET place_odds = latest.odds
        FROM latest
        WHERE rr.race_id = latest.race_id
          AND rr.horse_number::text = latest.horse_no_str
          AND rr.finish_position IS NOT NULL
          AND rr.place_odds IS NULL
    """
    from sqlalchemy import text as _text

    result = await db.execute(_text(sql), {"race_ids": race_ids})
    return getattr(result, "rowcount", 0) or 0


# -------------------------------------------------------------------
# エンドポイント
# -------------------------------------------------------------------


@chihou_router.post("/races")
async def chihou_import_races(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """地方競馬 RA/SE レコード（レース情報・出馬表・成績）を取り込む。

    UmaConn エージェントから呼び出される。
    JRA（keiba スキーマ）版と異なり、WebSocket ブロードキャストおよび
    指数算出トリガーは行わない（フロントエンドが地方競馬に未対応のため）。
    """
    importer = ChihouRaceImporter(db)
    records = [r.model_dump() for r in body.records]
    if records:
        first = records[0]
        logger.debug(
            "chihou recv: rec_id=%r data[:20]=%r total=%d",
            first.get("rec_id"),
            first.get("data", "")[:20],
            len(records),
        )
    stats = await importer.import_records(records)
    await db.commit()
    logger.info("chihou_import_races stats: %s", stats)

    # 成績が確定したレースをWebSocketでブロードキャスト
    result_race_ids: list[int] = stats.get("result_race_ids", [])  # type: ignore[assignment]
    for race_id in result_race_ids:
        payload = await _fetch_chihou_results_payload(race_id, db)
        if payload:
            await chihou_results_manager.broadcast(race_id, payload)  # type: ignore[arg-type]

    if result_race_ids:
        # 成績確定レースの日付を取得し、地方推奨の的中・払戻をバックグラウンド更新
        dates_result = await db.execute(
            select(ChihouRace.date).where(ChihouRace.id.in_(result_race_ids)).distinct()
        )
        confirmed_dates = [row[0] for row in dates_result.fetchall()]

        async def _update_chihou_results_bg(dates: list[str]) -> None:
            async with AsyncSessionLocal() as bg_session:
                for date in dates:
                    try:
                        n = await update_chihou_results(bg_session, date)
                        await bg_session.commit()
                        logger.info("地方推奨結果自動更新: date=%s updated=%d", date, n)
                    except Exception as e:
                        logger.warning("地方推奨結果自動更新失敗: date=%s err=%s", date, e)

        import asyncio
        asyncio.ensure_future(_update_chihou_results_bg(confirmed_dates))

    return {"ok": True, "stats": stats}


@chihou_router.post("/odds")
async def chihou_import_odds(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """地方競馬 O1-O8 オッズレコードを取り込む。

    chihou.odds_history テーブルへ格納する。
    JRA 版と異なり WebSocket ブロードキャストは行わない。
    """
    importer = ChihouOddsImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = await importer.import_records(records)
    await db.commit()
    logger.info("chihou_import_odds stats: %s", stats)
    return {"ok": True, "stats": stats}


@chihou_router.post("/bloodlines")
async def chihou_import_bloodlines(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """地方競馬 HN/SK レコード（血統データ）を取り込む。

    HN（繁殖馬マスタ）と SK（産駒マスタ）を同一バッチで送信すること。
    HN が先に処理されて in-memory 辞書を構築し、SK の馬名解決に使用する。
    """
    importer = ChihouPedigreeImporter(db)  # type: ignore[arg-type]
    records = [r.model_dump() for r in body.records]
    stats = importer.import_records(records)
    await db.commit()
    logger.info("chihou_import_bloodlines stats: %s", stats)
    return {"ok": True, "stats": stats}


@chihou_router.post("/payouts")
async def chihou_import_payouts(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """地方競馬 HR レコード（払戻情報）を取り込む。

    処理内容:
      0. 生 HR レコード（rec_id="HR", data=...）を parse_hr でパース
      1. umaconn_race_id で chihou.races.id を一括解決（N+1回避）
      2. chihou.race_payouts に一括 upsert（UNIQUE制約で重複排除）
      3. 複勝払戻は chihou.race_results.place_odds にも一括更新する
    """
    if not body.records:
        return {"imported": 0, "skipped": 0}

    # 0. 生 HR レコードをパース
    parsed_records = []
    for rec in body.records:
        raw = rec.model_dump()
        if raw.get("rec_id") == "HR":
            parsed = parse_hr(raw.get("data", ""))
            if parsed:
                parsed_records.append(parsed)
        else:
            logger.warning("chihou_import_payouts: unexpected rec_id=%r", raw.get("rec_id"))

    if not parsed_records:
        return {"imported": 0, "skipped": 0}

    # 1. 一括 race_id 解決（umaconn_race_id → DB id）
    umaconn_ids = list({hr["race_id"] for hr in parsed_records})
    race_rows = await db.execute(
        select(ChihouRace.id, ChihouRace.umaconn_race_id).where(
            ChihouRace.umaconn_race_id.in_(umaconn_ids)
        )
    )
    race_id_map: dict[str, int] = {r.umaconn_race_id: r.id for r in race_rows}

    # 2. 全 upsert 値を一括構築
    payout_values: list[dict] = []
    place_updates: list[tuple[int, int, Decimal]] = []  # (race_db_id, horse_number, odds)
    skipped = 0

    for hr in parsed_records:
        race_db_id = race_id_map.get(hr["race_id"])
        if race_db_id is None:
            logger.debug(
                "chihou_import_payouts: race not found for umaconn_race_id=%r", hr["race_id"]
            )
            skipped += len(hr.get("payouts", []))
            continue

        for entry in hr.get("payouts", []):
            payout_values.append({
                "race_id": race_db_id,
                "bet_type": entry["bet_type"],
                "combination": entry["combination"],
                "payout": entry["payout"],
                "popularity": entry.get("popularity"),
            })
            if entry["bet_type"] == "place" and entry["combination"].isdigit():
                horse_number = int(entry["combination"])
                place_odds_val = Decimal(str(round(entry["payout"] / 100, 1)))
                place_updates.append((race_db_id, horse_number, place_odds_val))

    # 3. 一括 upsert（chihou.race_payouts へ）
    imported = 0
    if payout_values:
        stmt = (
            pg_insert(ChihouRacePayout)
            .values(payout_values)
            .on_conflict_do_update(
                constraint="uq_chihou_race_payouts_race_type_combo",
                set_={
                    "payout": pg_insert(ChihouRacePayout).excluded.payout,
                    "popularity": pg_insert(ChihouRacePayout).excluded.popularity,
                },
            )
        )
        await db.execute(stmt)
        imported = len(payout_values)

    # 4. 複勝払戻を chihou.race_results.place_odds に一括反映
    if place_updates:
        pairs = [(r, h) for r, h, _ in place_updates]
        result_rows = await db.execute(
            select(ChihouRaceResult).where(
                sa_tuple(ChihouRaceResult.race_id, ChihouRaceResult.horse_number).in_(pairs)
            )
        )
        results_map: dict[tuple[int, int], ChihouRaceResult] = {
            (r.race_id, r.horse_number): r
            for r in result_rows.scalars()
            if r.race_id is not None and r.horse_number is not None
        }
        for race_db_id, horse_number, odds_val in place_updates:
            result = results_map.get((race_db_id, horse_number))
            if result is not None:
                result.place_odds = odds_val

    # 5. 着外馬の place_odds を odds_history の発走前最終スナップショットで補完
    #    HR 払戻は 1〜3着のみ → そのままだと race_results.place_odds の充足率が
    #    ~4% 止まり。複勝ROI バックテストの母集団確保のため全馬永続化する。
    affected_race_ids = list({r for r, _, _ in place_updates}) if place_updates else []
    if affected_race_ids:
        filled = await _fill_loser_place_odds_from_history(db, affected_race_ids)
        if filled:
            logger.info(
                "chihou_import_payouts: filled %d loser place_odds from odds_history", filled
            )

    await db.commit()
    logger.info("chihou_import_payouts: imported=%d, skipped=%d", imported, skipped)
    return {"imported": imported, "skipped": skipped}


async def _run_chihou_calculate(date: str) -> None:
    """バックグラウンドで指定日の地方指数を算出する（独立セッション）。"""
    async with AsyncSessionLocal() as db:
        try:
            races = (
                await db.execute(
                    select(ChihouRace.id, ChihouRace.race_number)
                    .where(and_(ChihouRace.date == date, ChihouRace.course != BANEI_COURSE_CODE))
                    .order_by(ChihouRace.id)
                )
            ).fetchall()
            if not races:
                logger.info(f"[chihou calculate] 対象レースなし: date={date}")
                return
            calc = ChihouIndexCalculator(db)
            saved_total = 0
            for race_id, race_num in races:
                stats = await calc.calculate_and_save(race_id)
                saved_total += stats.get("saved", 0)
            await db.commit()
            logger.info(f"[chihou calculate] 完了: date={date} races={len(races)} saved={saved_total}")
        except Exception as e:
            logger.error(f"[chihou calculate] 失敗: date={date} error={e}", exc_info=True)


@chihou_router.post("/calculate")
async def chihou_trigger_calculate(
    background_tasks: BackgroundTasks,
    _: ApiKeyDep,
    date: str = Query(description="算出対象日 YYYYMMDD"),
) -> dict:
    """指定日の地方競馬全レースについて指数をバックグラウンド算出する。

    UmaConn Agent の daily フェッチ後に自動で呼び出される。
    """
    background_tasks.add_task(_run_chihou_calculate, date)
    logger.info(f"[chihou calculate] バックグラウンドタスク登録: date={date}")
    return {"ok": True, "date": date, "message": "Chihou calculation started in background"}


@chihou_router.post("/weights")
async def chihou_import_weights(
    body: WeightRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """地方競馬 馬体重レコード（WE/SE の一部）を取り込む。

    馬体重データは SE レコードと同一パスで処理するため ChihouRaceImporter を使用する。
    """
    importer = ChihouRaceImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = await importer.import_records(records)
    await db.commit()
    return {"ok": True, "stats": stats}
