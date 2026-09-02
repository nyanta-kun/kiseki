"""インポートAPIルーター

Windows Agent からのJV-Linkデータ受信エンドポイント。
X-API-Key ヘッダーで簡易認証を行う。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import (
    OddsHistory,
    Race,
    RaceEntry,
    RacePayout,
    RaceResult,
    SpecialRegistration,
    Win5Event,
    Win5Leg,
    Win5Payout,
)
from ..db.session import AsyncSessionLocal, get_db
from ..importers import (
    ChangeHandler,
    OddsImporter,
    PedigreeImporter,
    RaceImporter,
    TrainingImporter,
)
from ..importers.jvlink_parser import COURSE_NAMES, parse_we, parse_wf, parse_wh
from ..importers.provisional_horse_importer import upsert_provisional_horses
from ..indices.composite import CompositeIndexCalculator
from ..services.recommender import update_results as update_recommendation_results
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


class TrackConditionRequest(BaseModel):
    """天候馬場状態レコード（WE / 速報系 0B14）。"""

    date: str
    records: list[JvRecord]


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

    # 成績が確定したレースをWebSocketでブロードキャスト + 推奨結果を更新
    result_race_ids: list[int] = stats.get("result_race_ids", [])  # type: ignore[assignment]
    for race_id in result_race_ids:
        payload = await _fetch_results_payload(race_id, db)
        if payload:
            await results_manager.broadcast(race_id, payload)  # type: ignore[arg-type]

    if result_race_ids:
        # 成績確定レースの日付を取得し、推奨結果をバックグラウンド更新
        dates_result = await db.execute(
            select(Race.date).where(Race.id.in_(result_race_ids)).distinct()
        )
        confirmed_dates = [row[0] for row in dates_result.fetchall()]

        async def _update_results_bg(dates: list[str]) -> None:
            async with AsyncSessionLocal() as bg_session:
                for date in dates:
                    try:
                        n = await update_recommendation_results(bg_session, date)
                        logger.info("推奨結果自動更新: date=%s updated=%d", date, n)
                    except Exception as e:
                        logger.warning("推奨結果自動更新失敗: date=%s err=%s", date, e)

        import asyncio
        asyncio.ensure_future(_update_results_bg(confirmed_dates))

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
    stats = await importer.import_records(records)
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
    stats = await importer.import_records(records)
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


async def _weight_coverage_by_race(db: AsyncSession, date: str) -> dict[int, int]:
    """指定日のレースごとに horse_weight が入っている出走馬数を返す。"""
    rows = await db.execute(
        select(RaceEntry.race_id, func.count(RaceEntry.horse_weight))
        .join(Race, Race.id == RaceEntry.race_id)
        .where(Race.date == date)
        .group_by(RaceEntry.race_id)
    )
    return {race_id: count for race_id, count in rows.all()}


async def _recalculate_races(race_ids: list[int]) -> None:
    """指定レースの総合指数を再算出する（独立セッション）。"""
    async with AsyncSessionLocal() as db:
        calc = CompositeIndexCalculator(db)
        for race_id in race_ids:
            try:
                await calc.calculate_and_save(race_id)
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"[recalc] 失敗: race_id={race_id} error={e}", exc_info=True)
        logger.info(f"[recalc] 馬体重反映で {len(race_ids)} レースを再算出")


async def _apply_wh_records(db: AsyncSession, records: list[dict]) -> int:
    """WH（速報馬体重）を race_entries.horse_weight / weight_change へ反映する。

    Returns: 更新した出走馬数。
    """
    updated = 0
    for rec in records:
        parsed = parse_wh(rec.get("data", ""))
        if not parsed:
            continue

        race_id = (await db.execute(
            select(Race.id).where(Race.jravan_race_id == parsed["jravan_race_id"])
        )).scalar_one_or_none()
        if race_id is None:
            continue

        for e in parsed["entries"]:
            if e["horse_weight"] is None:
                # "000"=出走取消 / "999"=計量不能。既存値を None で潰さない。
                continue
            result = await db.execute(
                update(RaceEntry)
                .where(RaceEntry.race_id == race_id)
                .where(RaceEntry.horse_number == e["horse_number"])
                .values(horse_weight=e["horse_weight"], weight_change=e["weight_change"])
            )
            updated += getattr(result, "rowcount", 0) or 0
    return updated


@router.post("/weights")
async def import_weights(
    body: WeightRequest,
    background_tasks: BackgroundTasks,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """馬体重レコード（0B11 の WH / 一部 SE）を取り込む。

    ⚠️ **2026-08-08 まで WH は捨てられていた。** 本エンドポイントは受け取った
    レコードを RaceImporter へ渡すだけで、RaceImporter は rec_id が RA/SE の
    ものしか見ない。0B11 が返すのは全て WH なので、23件/回が毎回まるごと無視され
    200 が返っていた。結果 `race_entries.horse_weight` は 0B12（確定成績）経由で
    **1〜3着馬にしか**入らず、v27 の特徴量 `horse_weight` / `weight_change` は
    当日の指数算出で常に欠損していた（レース内 sd が実測で約半分に潰れる）。

    馬体重が新たに入ったレースはその場で再算出する。当日の一括算出は VPS cron の
    07:30 JST 一回きりで、馬体重が届くのは発走の約1時間前なので、ここで拾わないと
    当日の指数は最後まで馬体重なしのままになる。

    realtime ループは同じ 0B11 を約30秒ごとに投げてくるので、**充足数が増えた
    レースだけ**を対象にする。全馬そろったあとの再送では差分が出ず再算出は走らない。
    """
    before = await _weight_coverage_by_race(db, body.date)

    records = [r.model_dump() for r in body.records]
    wh_records = [r for r in records if r.get("rec_id") == "WH"]
    other_records = [r for r in records if r.get("rec_id") != "WH"]

    wh_updated = await _apply_wh_records(db, wh_records)

    importer = RaceImporter(db)  # type: ignore[arg-type]
    stats = await importer.import_records(other_records)
    stats["weights"] = wh_updated
    await db.commit()

    after = await _weight_coverage_by_race(db, body.date)
    changed = [rid for rid, cnt in after.items() if cnt > before.get(rid, 0)]
    if changed:
        background_tasks.add_task(_recalculate_races, changed)
        logger.info(f"[recalc] 馬体重が増えたレースを再算出登録: {len(changed)} 件 date={body.date}")

    return {"ok": True, "stats": stats, "recalculated_races": len(changed)}


@router.post("/track-conditions")
async def import_track_conditions(
    body: TrackConditionRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """WEレコード（天候馬場状態）から races.condition / weather を発走前に更新する。

    RA の馬場状態は成績確定後にしか入らないため、当日の指数算出時点では
    馬場状態が不明だった（`going_pedigree_index` が全馬ニュートラルに固定される）。
    WE は当日朝（実測 06:55）に配信されるため、これを取り込めば発走前に確定する。

    WE は **開催（競馬場）単位** で、芝とダートで別々の馬場状態を持つ。
    「変更なし」の項目は None で来るので、発表時刻順に非 None のみ上書き適用し、
    最終状態をその日のその競馬場の全レースへ、`surface` に応じて反映する。
    """
    parsed = [
        p for p in (parse_we(r.data) for r in body.records) if p is not None
    ]
    if not parsed:
        return {"ok": True, "updated": 0, "note": "WEレコードなし"}

    # (date, course) ごとに発表時刻順で最終状態を畳み込む。
    # announced_at=None（初期状態）を先頭に置き、以降を時刻順に適用する。
    latest: dict[tuple[str, str], dict[str, str | None]] = {}
    for p in sorted(parsed, key=lambda x: (x["announced_at"] or "")):
        key = (p["date"], p["course"])
        cur = latest.setdefault(key, {"weather": None, "turf": None, "dirt": None})
        if p["weather"]:
            cur["weather"] = p["weather"]
        if p["turf_condition"]:
            cur["turf"] = p["turf_condition"]
        if p["dirt_condition"]:
            cur["dirt"] = p["dirt_condition"]

    updated = 0
    for (date, course), st in latest.items():
        races = (
            await db.execute(
                select(Race).where(Race.date == date, Race.course == course)
            )
        ).scalars().all()
        for race in races:
            surface = (race.surface or "").strip()
            # 障害は芝・ダートを併用するが、JRA の発表上は芝馬場状態に従う
            cond = st["dirt"] if surface.startswith("ダ") else st["turf"]
            changed = False
            if cond and race.condition != cond:
                race.condition = cond
                changed = True
            if st["weather"] and race.weather != st["weather"]:
                race.weather = st["weather"]
                changed = True
            if changed:
                updated += 1

    await db.commit()
    logger.info(
        f"[track-conditions] date={body.date} 開催={len(latest)} 更新={updated}レース"
    )
    return {"ok": True, "updated": updated, "venues": len(latest)}


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
    importer = PedigreeImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = await importer.import_records(records)
    await db.commit()
    logger.info(f"import_bloodlines: {stats}")
    return {"ok": True, "stats": stats}


@router.post("/training")
async def import_training(
    body: ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """HC（坂路）/ WC（ウッドチップ）調教レコードを取り込む。

    Windows Agent の chokyo モードから raw レコード（{rec_id, data}）で送信される。
    parse_hc / parse_wc でパースし keiba.slope_training / wood_training へ UPSERT する。
    """
    importer = TrainingImporter(db)
    records = [r.model_dump() for r in body.records]
    stats = await importer.import_records(records)
    await db.commit()
    logger.info(f"import_training: {stats}")
    return {"ok": True, "stats": stats}


class ProvisionalHorseRecord(BaseModel):
    netkeiba_horse_id: str
    name: str
    birth_year: int | None = None
    birth_date: str | None = None
    sex: str | None = None
    coat_color: str | None = None
    sire_name: str | None = None
    dam_name: str | None = None
    broodmare_sire_name: str | None = None
    trainer_name: str | None = None
    owner_name: str | None = None
    farm_name: str | None = None


class ProvisionalHorsesImportRequest(BaseModel):
    horses: list[ProvisionalHorseRecord]


@router.post("/provisional-horses")
async def import_provisional_horses(
    body: ProvisionalHorsesImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """netkeiba スクレイプ馬データを provisional_horses へ UPSERT する。

    JV-Link 未登録の2歳馬（競走馬登録前）を暫定保存する。
    初出走時（SE レコード到着時）に自動で keiba.horses へマージされる。
    """
    records = [r.model_dump() for r in body.horses]
    stats = await upsert_provisional_horses(db, records)
    await db.commit()
    logger.info("import_provisional_horses: %s", stats)
    return {"ok": True, "stats": stats}


@router.post("/payouts")
async def import_payouts(
    body: PayoutsImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """HR レコード（払戻情報）を取り込む。

    処理内容:
      1. jravan_race_id で races.id を一括解決（N+1回避）
      2. race_payouts に一括 upsert（UNIQUE制約で重複排除）
      3. 複勝払戻は race_results.place_odds にも一括更新する
    """
    from decimal import Decimal

    if not body.records:
        return {"imported": 0, "skipped": 0}

    # 1. 一括 race_id 解決（jravan_race_id → DB id）
    jravan_ids = list({hr.race_id for hr in body.records})
    race_rows = await db.execute(
        select(Race.id, Race.jravan_race_id).where(Race.jravan_race_id.in_(jravan_ids))
    )
    race_id_map: dict[str, int] = {r.jravan_race_id: r.id for r in race_rows}

    # 2. 全 upsert 値を一括構築
    payout_values: list[dict] = []
    place_updates: list[tuple[int, int, Decimal]] = []  # (race_db_id, horse_number, odds)
    skipped = 0

    for hr in body.records:
        race_db_id = race_id_map.get(hr.race_id)
        if race_db_id is None:
            logger.debug(f"import_payouts: race not found for jravan_race_id={hr.race_id!r}")
            skipped += len(hr.payouts)
            continue

        for entry in hr.payouts:
            payout_values.append({
                "race_id": race_db_id,
                "bet_type": entry.bet_type,
                "combination": entry.combination,
                "payout": entry.payout,
                "popularity": entry.popularity,
            })
            if entry.bet_type == "place" and entry.combination.isdigit():
                horse_number = int(entry.combination)
                place_odds_val = Decimal(str(round(entry.payout / 100, 1)))
                place_updates.append((race_db_id, horse_number, place_odds_val))

    # 3. 一括 upsert（race_payoutsへ）
    imported = 0
    if payout_values:
        stmt = (
            pg_insert(RacePayout)
            .values(payout_values)
            .on_conflict_do_update(
                constraint="uq_race_payouts_race_type_combo",
                set_={
                    "payout": pg_insert(RacePayout).excluded.payout,
                    "popularity": pg_insert(RacePayout).excluded.popularity,
                },
            )
        )
        await db.execute(stmt)
        imported = len(payout_values)

    # 4. 複勝払戻を race_results.place_odds に一括反映
    if place_updates:
        # (race_id, horse_number) ペアで一括取得
        from sqlalchemy import tuple_ as sa_tuple
        pairs = [(r, h) for r, h, _ in place_updates]
        result_rows = await db.execute(
            select(RaceResult).where(
                sa_tuple(RaceResult.race_id, RaceResult.horse_number).in_(pairs)
            )
        )
        results_map: dict[tuple[int, int], RaceResult] = {
            (r.race_id, r.horse_number): r for r in result_rows.scalars()
            if r.race_id is not None and r.horse_number is not None
        }
        for race_db_id, horse_number, odds_val in place_updates:
            result = results_map.get((race_db_id, horse_number))
            if result is not None:
                result.place_odds = odds_val

    await db.commit()
    logger.info(f"import_payouts: imported={imported}, skipped={skipped}")
    return {"imported": imported, "skipped": skipped}


# -------------------------------------------------------------------
# JRA-VAN NEXT DM指数インポート
# -------------------------------------------------------------------

class JvanDmRecord(BaseModel):
    """JRA-VAN NEXT DM指数1件。"""

    jravan_race_id: str  # 16文字 (例: "2026042503010501")
    horse_number: int
    jvan_time_dm: float | None = None
    jvan_battle_dm: float | None = None


class JvanDmRequest(BaseModel):
    """JRA-VAN NEXT DM指数インポートリクエスト。"""

    records: list[JvanDmRecord]


@router.post("/jvan_dm")
async def import_jvan_dm(
    body: JvanDmRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """JRA-VAN NEXT のタイム型DM・対戦型DM指数を race_entries に格納する。

    Windows Agent がローカルキャッシュ (1403/*.dat) から抽出したDM値を受信。
    jravan_race_id + horse_number でエントリーを特定して UPDATE する。
    """
    from ..db.models import RaceEntry

    if not body.records:
        return {"updated": 0, "skipped": 0}

    # jravan_race_id → races.id の一括解決
    jravan_ids = list({r.jravan_race_id for r in body.records})
    race_rows = await db.execute(
        select(Race.id, Race.jravan_race_id).where(Race.jravan_race_id.in_(jravan_ids))
    )
    race_id_map: dict[str, int] = {r.jravan_race_id: r.id for r in race_rows}

    # race_entry を (race_id, horse_number) キーで一括取得
    found_race_ids = list(race_id_map.values())
    if not found_race_ids:
        return {"updated": 0, "skipped": len(body.records)}

    entry_rows = await db.execute(
        select(RaceEntry).where(RaceEntry.race_id.in_(found_race_ids))
    )
    entry_map: dict[tuple[int, int], RaceEntry] = {
        (e.race_id, e.horse_number): e for e in entry_rows.scalars()
    }

    updated = 0
    skipped = 0
    for rec in body.records:
        db_race_id = race_id_map.get(rec.jravan_race_id)
        if db_race_id is None:
            skipped += 1
            continue
        entry = entry_map.get((db_race_id, rec.horse_number))
        if entry is None:
            skipped += 1
            continue
        if rec.jvan_time_dm is not None:
            entry.jvan_time_dm = Decimal(str(rec.jvan_time_dm))
        if rec.jvan_battle_dm is not None:
            entry.jvan_battle_dm = Decimal(str(rec.jvan_battle_dm))
        updated += 1

    await db.commit()
    logger.info(f"import_jvan_dm: updated={updated}, skipped={skipped}")
    return {"updated": updated, "skipped": skipped}


class TokuRecord(BaseModel):
    """TK レコード由来の特別登録馬 1 頭分。"""

    jravan_race_id: str
    race_date: str
    course_code: str
    race_number: int
    jravan_horse_code: str
    horse_name: str
    sex: str | None = None
    age: int | None = None
    east_west_code: str | None = None
    jravan_trainer_code: str | None = None
    trainer_name: str | None = None
    data_type: str | None = None
    race_name: str | None = None
    grade_code: str | None = None
    distance: int | None = None
    track_code: str | None = None
    race_type_code: str | None = None  # 馬齢条件コード（TK pos 506-507 由来）


class TokuImportRequest(BaseModel):
    """特別登録馬インポートリクエスト。

    entries: 通常 POST（jvlink_agent の on_toku_file_done から送信）
    records: retry_pending が {"records": batch} 形式で再送する場合に使用
    """

    entries: list[TokuRecord] | None = None
    records: list[TokuRecord] | None = None

    @property
    def all_entries(self) -> list[TokuRecord]:
        return self.entries or self.records or []


@router.post("/toku")
async def import_toku(
    body: TokuImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """特別登録馬（TK レコード）を special_registrations テーブルに格納する。

    Windows Agent の run_toku から呼び出される。
    同一 (jravan_race_id, jravan_horse_code) は UPSERT で更新する。
    """
    entries = body.all_entries
    if not entries:
        return {"upserted": 0, "skipped": 0}

    rows = [
        {
            "jravan_race_id": e.jravan_race_id,
            "race_date": e.race_date,
            "course_code": e.course_code,
            "race_number": e.race_number,
            "jravan_horse_code": e.jravan_horse_code,
            "horse_name": e.horse_name,
            "sex": e.sex,
            "age": e.age,
            "east_west_code": e.east_west_code,
            "jravan_trainer_code": e.jravan_trainer_code,
            "trainer_name": e.trainer_name,
            "data_type": e.data_type,
            "race_name": e.race_name,
            "grade_code": e.grade_code,
            "distance": e.distance,
            "track_code": e.track_code,
        }
        for e in entries
    ]

    # races テーブルに placeholder を作る（出馬表確定前のレースを一覧/詳細で引けるように）。
    # 既存レコードあればスキップ。出馬表確定後の RA 取込で UPDATE される。
    # surface は track_code から推定（10-19=芝, 20-29=ダート, 51-=障害）。
    def _surface_from_track(tc: str | None) -> str:
        if not tc or len(tc) < 1:
            return ""
        t = tc[0]
        return "芝" if t == "1" else ("ダ" if t == "2" else ("障" if t == "5" else ""))

    # TK の grade_code (1 char) → races.grade 表示用文字列
    _GRADE_LABEL = {
        "A": "G1", "B": "G2", "C": "G3",
        "D": "J.G1", "E": None, "F": "J.G3",  # E は条件/特別なので grade NULL
        "L": "Listed",
    }

    race_seen: dict[str, dict] = {}
    for e in entries:
        if e.jravan_race_id in race_seen:
            continue
        race_seen[e.jravan_race_id] = {
            "jravan_race_id": e.jravan_race_id,
            "date": e.race_date,
            "course": e.course_code,
            "course_name": COURSE_NAMES.get(e.course_code, e.course_code),
            "race_number": e.race_number,
            "race_name": e.race_name,
            "surface": _surface_from_track(e.track_code),
            "distance": e.distance or 0,
            "grade": _GRADE_LABEL.get(e.grade_code or "", None),
            "race_type_code": e.race_type_code,
        }
    if race_seen:
        race_stmt = pg_insert(Race).values(list(race_seen.values()))
        # race_type_code は RA 取込後に正式値で上書きされる。
        # TOKU 由来の値（馬齢条件コード）は race_class_label の部分表示に使用する。
        # 既存レコードに race_type_code が未設定の場合のみ補完する（RA 上書き保護）。
        race_stmt = race_stmt.on_conflict_do_update(
            index_elements=["jravan_race_id"],
            set_={
                "race_type_code": func.coalesce(
                    Race.__table__.c.race_type_code,
                    race_stmt.excluded.race_type_code,
                ),
            },
        )
        await db.execute(race_stmt)

    stmt = pg_insert(SpecialRegistration).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_special_reg_race_horse",
        set_={
            "horse_name": stmt.excluded.horse_name,
            "sex": stmt.excluded.sex,
            "age": stmt.excluded.age,
            "east_west_code": stmt.excluded.east_west_code,
            "jravan_trainer_code": stmt.excluded.jravan_trainer_code,
            "trainer_name": stmt.excluded.trainer_name,
            "data_type": stmt.excluded.data_type,
            "race_name": stmt.excluded.race_name,
            "grade_code": stmt.excluded.grade_code,
            "distance": stmt.excluded.distance,
            "track_code": stmt.excluded.track_code,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)
    await db.commit()

    logger.info(f"import_toku: upserted={len(rows)}")
    return {"upserted": len(rows), "skipped": 0}


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


# -------------------------------------------------------------------
# 指数算出トリガー
# -------------------------------------------------------------------

async def _run_calculate(date: str) -> None:
    """バックグラウンドで指定日の指数を算出する（独立セッション）。"""
    async with AsyncSessionLocal() as db:
        try:
            calc = CompositeIndexCalculator(db)
            logger.info(f"[calculate] 開始: date={date}")
            rows = await calc.calculate_batch_for_date(date)
            await db.commit()
            logger.info(f"[calculate] 完了: {len(rows)} 件保存 date={date}")
        except Exception as e:
            logger.error(f"[calculate] 失敗: date={date} error={e}", exc_info=True)


@router.post("/calculate")
async def trigger_calculate(
    background_tasks: BackgroundTasks,
    _: ApiKeyDep,
    date: str = Query(description="算出対象日 YYYYMMDD"),
) -> dict:
    """指定日の全レースについて総合指数をバックグラウンド算出する。

    Windows Agent の daily フェッチ後に自動で呼び出される。
    既存の calculated_indices レコードは UPSERT で上書きされる。
    """
    if len(date) != 8 or not date.isdigit():
        raise HTTPException(status_code=400, detail="date must be YYYYMMDD")
    background_tasks.add_task(_run_calculate, date)
    logger.info(f"[calculate] バックグラウンドタスク登録: date={date}")
    return {"ok": True, "date": date, "message": "Calculation started in background"}


# -------------------------------------------------------------------
# WIN5（重勝式）インポート
#
# 🔴 **専用経路にする理由。** WF は RaceImporter が見ない（RA/SE しか
# 処理しない）。0B11（速報馬体重）は「取り込むコードはあるのに rec_id の
# 振り分け漏れで全件捨てられ、200 が返り続けていた」という事故を起こした。
# WF も同じ構造なので、汎用の import 経路に混ぜず専用の口を用意し、
# **経路そのものをテストで固定する**（tests/test_win5_import_route.py）。
# -------------------------------------------------------------------


class Win5LegRecord(BaseModel):
    """WF 項番7（対象レース）+ 項番10（有効票数）の1脚分。"""

    leg_no: int
    jravan_race_id: str
    valid_votes: int | None = None


class Win5PayoutRecord(BaseModel):
    """WF 項番16（重勝式払戻情報）の1件。"""

    combination: str
    payout: int | None = None
    hit_votes: int | None = None


class Win5Record(BaseModel):
    """parse_wf の戻り値に対応する1レコード。"""

    rec_id: str = "WF"
    data_kubun: str | None = None
    created_date: str | None = None
    held_date: str
    sold_votes: int | None = None
    refund_flag: bool | None = None
    void_flag: bool | None = None
    no_hit_flag: bool | None = None
    carryover_start: int | None = None
    carryover_balance: int | None = None
    legs: list[Win5LegRecord] = []
    payouts: list[Win5PayoutRecord] = []


class Win5ImportRequest(BaseModel):
    """WIN5 インポートリクエスト。

    🔴 **生の WF レコードを受け取り、パースは**サーバ側**で行う。**
    エージェント側でパースしない理由（2026-09-02 に実機で判明）:

    - `windows-agent/jvlink_parser.py` は **git 管理外**で、実機のものは
      2026-05-04 付と4か月古い。更新手順も自動化もどこにも無い
    - main のパーサは `from ..bet_types import BET_TYPES` という**相対 import**を
      持つため、そのまま実機へ置くと単体 import できず、**既存の HR 払戻経路
      （`from jvlink_parser import parse_hr`）まで巻き込んで壊れる**（実測）

    `/api/import/weights`（0B11）が同じ理由で「エージェントは生レコードを
    POST し、サーバが `parse_wh` する」形になっている。WIN5 もそれに揃える。
    こうすると**実機のパーサを一切更新しなくてよい**。
    """

    records: list[JvRecord]


# 区分1（重勝式詳細発表時）では払戻・有効票数・キャリーオーバー残高が未設定。
# 後から届いた区分1で区分7（成績）の確定値を潰さないための判定に使う。
_PRELIMINARY_KUBUN = {"1"}


@router.post("/win5")
async def import_win5(
    body: Win5ImportRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> dict:
    """WF レコード（重勝式 WIN5）を取り込む。

    ⚠️ **同じ開催日について WF は区分 1→2→3→7 と複数回届く。**
    区分1 は払戻・有効票数・キャリーオーバー残高が未設定なので、
    **後から届いた区分1で区分7（成績）の確定値を潰さない**。
    値が None のフィールドは上書きしない（races.condition と同じ COALESCE 作法）。

    🔴 **`unresolved_races` を必ず返す。** 合成した16桁 `jravan_race_id` が
    `races` に1件も一致しないのに 200 が返るのが 0B11 型の失敗形なので、
    呼び出し側が件数で気づけるようにする。

    Returns:
        imported: 取り込んだ開催数
        legs: 書き込んだ脚の数
        payouts: 書き込んだ的中組合せの数
        unresolved_races: `races` に解決できなかった脚の数（**0 を確認すること**）
        skipped_preliminary: 確定値を守るために上書きを見送ったフィールド数
    """
    if not body.records:
        return {"imported": 0, "legs": 0, "payouts": 0, "unresolved_races": 0,
                "skipped_preliminary": 0, "parsed": 0, "unparsed": 0}

    # 0. サーバ側でパースする（エージェントのパーサは古く、更新手段も無い）
    parsed_records: list[Win5Record] = []
    unparsed = 0
    for raw in body.records:
        if raw.rec_id != "WF":
            unparsed += 1
            continue
        parsed = parse_wf(raw.data)
        if parsed is None:
            unparsed += 1
            continue
        parsed_records.append(Win5Record.model_validate(parsed))
    if not parsed_records:
        logger.warning("import_win5: WF を1件もパースできませんでした（受信 %d 件）",
                       len(body.records))
        return {"imported": 0, "legs": 0, "payouts": 0, "unresolved_races": 0,
                "skipped_preliminary": 0, "parsed": 0, "unparsed": unparsed}

    records = parsed_records

    # 1. 対象レースの jravan_race_id を一括解決（N+1 回避）
    all_ids = {leg.jravan_race_id for rec in records for leg in rec.legs}
    race_id_map: dict[str, int] = {}
    if all_ids:
        rows = await db.execute(
            select(Race.id, Race.jravan_race_id).where(Race.jravan_race_id.in_(all_ids))
        )
        race_id_map = {r.jravan_race_id: r.id for r in rows}

    imported = n_legs = n_payouts = unresolved = skipped_prelim = 0

    for rec in records:
        is_prelim = rec.data_kubun in _PRELIMINARY_KUBUN

        event_values: dict[str, object] = {
            "held_date": rec.held_date,
            "data_kubun": rec.data_kubun,
            "created_date": rec.created_date,
            "sold_votes": rec.sold_votes,
            "refund_flag": rec.refund_flag,
            "void_flag": rec.void_flag,
            "no_hit_flag": rec.no_hit_flag,
            "carryover_start": rec.carryover_start,
            "carryover_balance": rec.carryover_balance,
        }
        # 確定値を空データで潰さない。None のフィールドは上書き対象から外す
        update_set = {k: v for k, v in event_values.items()
                      if k != "held_date" and v is not None}
        skipped_prelim += sum(1 for k, v in event_values.items()
                              if k != "held_date" and v is None)

        stmt = pg_insert(Win5Event).values(event_values)
        if update_set:
            stmt = stmt.on_conflict_do_update(
                constraint="uq_win5_events_held_date",
                set_={k: getattr(stmt.excluded, k) for k in update_set},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(constraint="uq_win5_events_held_date")
        await db.execute(stmt)

        event_id = (await db.execute(
            select(Win5Event.id).where(Win5Event.held_date == rec.held_date)
        )).scalar_one()
        imported += 1

        for leg in rec.legs:
            race_db_id = race_id_map.get(leg.jravan_race_id)
            if race_db_id is None:
                unresolved += 1
                logger.warning(
                    "import_win5: race not found for jravan_race_id=%r (held_date=%s leg=%d)",
                    leg.jravan_race_id, rec.held_date, leg.leg_no,
                )
            leg_values = {
                "win5_event_id": event_id,
                "leg_no": leg.leg_no,
                "jravan_race_id": leg.jravan_race_id,
                "race_id": race_db_id,
                "valid_votes": leg.valid_votes,
            }
            leg_set = {k: v for k, v in leg_values.items()
                       if k not in ("win5_event_id", "leg_no") and v is not None}
            leg_stmt = pg_insert(Win5Leg).values(leg_values)
            if leg_set:
                leg_stmt = leg_stmt.on_conflict_do_update(
                    constraint="uq_win5_legs_event_leg",
                    set_={k: getattr(leg_stmt.excluded, k) for k in leg_set},
                )
            else:
                leg_stmt = leg_stmt.on_conflict_do_nothing(
                    constraint="uq_win5_legs_event_leg")
            await db.execute(leg_stmt)
            n_legs += 1

        # 区分1 は払戻が未設定なので、既存の確定払戻を消さないよう何もしない
        if is_prelim and not rec.payouts:
            continue
        for pay in rec.payouts:
            pay_stmt = pg_insert(Win5Payout).values(
                win5_event_id=event_id,
                combination=pay.combination,
                payout=pay.payout,
                hit_votes=pay.hit_votes,
            )
            pay_stmt = pay_stmt.on_conflict_do_update(
                constraint="uq_win5_payouts_event_combo",
                set_={"payout": pay_stmt.excluded.payout,
                      "hit_votes": pay_stmt.excluded.hit_votes},
            )
            await db.execute(pay_stmt)
            n_payouts += 1

    await db.commit()
    logger.info(
        "import_win5: imported=%d legs=%d payouts=%d unresolved_races=%d",
        imported, n_legs, n_payouts, unresolved,
    )
    return {
        "imported": imported,
        "legs": n_legs,
        "payouts": n_payouts,
        "unresolved_races": unresolved,
        "skipped_preliminary": skipped_prelim,
        "parsed": len(records),
        "unparsed": unparsed,
    }
