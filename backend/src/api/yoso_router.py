"""予想管理APIルーター

ログインユーザーごとの印・指数投入、他ユーザー表示設定、成績集計を提供する。
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Horse,
    Race,
    RaceEntry,
    RacePayout,
    RaceResult,
    User,
    UserDisplaySetting,
    UserImport,
    UserPrediction,
)
from ..db.session import get_db
from ..indices.composite import COMPOSITE_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/yoso", tags=["yoso"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

# 有効な印セット
VALID_MARKS = {"◎", "○", "▲", "△", "×"}

# TARGET CSVヘッダー（仮フォーマット v1）
# race_date,course_code,race_no,horse_no,index
TARGET_CSV_REQUIRED_COLS = {"race_date", "course_code", "race_no", "horse_no", "index"}

# JRA 2桁コード → 競馬場名（レースID照合用）
_JRA_COURSE_MAP: dict[str, str] = {
    "01": "01", "02": "02", "03": "03", "04": "04",
    "05": "05", "06": "06", "07": "07", "08": "08",
    "09": "09", "10": "10",
}


# ---------------------------------------------------------------------------
# 認証ヘルパー（X-User-Email ヘッダー経由でDBユーザーを取得）
# ---------------------------------------------------------------------------
async def get_current_user(
    x_user_email: Annotated[str | None, Depends(lambda: None)] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """リクエストヘッダー X-User-Email からDBユーザーを取得する。"""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Use get_current_user_from_email")


async def _get_user_by_email(email: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------
class PredictionIn(BaseModel):
    """印・指数の保存リクエスト（1馬）"""
    race_id: int
    horse_id: int
    mark: str | None = None        # ◎○▲△× or None
    user_index: float | None = None


class PredictionOut(BaseModel):
    """予想レスポンス（1馬）"""
    horse_id: int
    horse_number: int
    horse_name: str
    frame_number: int | None
    mark: str | None
    user_index: float | None
    index_share: float | None      # 占有率 = user_index / Σ user_index（レース全馬）
    galloplab_index: float | None  # GallopLab AI複合指数
    win_odds: float | None
    place_odds: float | None
    finish_position: int | None


class RacePredictionOut(BaseModel):
    """レース単位の予想レスポンス"""
    race_id: int
    race_name: str | None
    race_number: int
    course_name: str
    horses: list[PredictionOut]
    other_users: list[OtherUserPredictionOut]


class OtherUserPredictionOut(BaseModel):
    """他ユーザーの予想（表示設定に従い）"""
    user_id: int
    user_name: str | None
    show_index: bool
    predictions: list[OtherHorsePrediction]


class OtherHorsePrediction(BaseModel):
    horse_id: int
    mark: str | None
    user_index: float | None  # show_index=False の場合は None


class DisplaySettingOut(BaseModel):
    target_user_id: int
    target_user_name: str | None
    target_user_email: str
    target_can_input_index: bool
    show_mark: bool
    show_index: bool


class DisplaySettingIn(BaseModel):
    target_user_id: int
    show_mark: bool
    show_index: bool


class StatsOut(BaseModel):
    """成績集計レスポンス"""
    # 印別
    by_mark: list[MarkStats]
    # 指数帯別
    by_index_range: list[IndexRangeStats]
    # 占有率帯別
    by_share_range: list[ShareRangeStats]


class MarkStats(BaseModel):
    mark: str
    count: int
    win_count: int
    place_count: int
    win_rate: float
    place_rate: float
    win_roi: float    # 単勝回収率
    place_roi: float  # 複勝回収率


class IndexRangeStats(BaseModel):
    label: str        # 例: "70〜79"
    min_val: float
    max_val: float | None
    count: int
    win_rate: float
    place_rate: float
    win_roi: float
    place_roi: float


class ShareRangeStats(BaseModel):
    label: str        # 例: "15%〜20%"
    min_val: float
    max_val: float | None
    count: int
    win_rate: float
    place_rate: float
    win_roi: float
    place_roi: float


class ImportLogOut(BaseModel):
    id: int
    filename: str
    race_date: str
    total_count: int
    saved_count: int
    error_count: int
    created_at: datetime


# ---------------------------------------------------------------------------
# エンドポイント: 予想一覧（日付別）
# ---------------------------------------------------------------------------
@router.get("/races/{date}", response_model=list[RacePredictionOut])
async def get_yoso_races(
    date: str,
    x_user_email: Annotated[str, Query()],
    db: DbDep,
) -> list[RacePredictionOut]:
    """指定日のレース一覧と自分の予想・他ユーザー印を返す。

    Args:
        date: YYYYMMDD 形式の開催日
        x_user_email: 認証済みユーザーのメールアドレス
    """
    me = await _get_user_by_email(x_user_email, db)

    # 対象日のレース取得
    races_result = await db.execute(
        select(Race).where(Race.date == date).order_by(Race.course_name, Race.race_number)
    )
    races = races_result.scalars().all()
    if not races:
        return []

    race_ids = [r.id for r in races]

    # 出走馬取得
    entries_result = await db.execute(
        select(RaceEntry, Horse)
        .join(Horse, RaceEntry.horse_id == Horse.id)
        .where(RaceEntry.race_id.in_(race_ids))
        .order_by(RaceEntry.race_id, RaceEntry.horse_number)
    )
    entries_by_race: dict[int, list[tuple[RaceEntry, Horse]]] = {}
    for entry, horse in entries_result.all():
        entries_by_race.setdefault(entry.race_id, []).append((entry, horse))

    # 自分の予想取得
    my_preds_result = await db.execute(
        select(UserPrediction).where(
            UserPrediction.user_id == me.id,
            UserPrediction.race_id.in_(race_ids),
        )
    )
    my_preds: dict[tuple[int, int], UserPrediction] = {
        (p.race_id, p.horse_id): p for p in my_preds_result.scalars().all()
    }

    # GallopLab AI指数取得（calculated_indices）
    from ..db.models import CalculatedIndex  # 循環避けローカルインポート
    ci_result = await db.execute(
        select(CalculatedIndex).where(
            CalculatedIndex.race_id.in_(race_ids),
            CalculatedIndex.version == COMPOSITE_VERSION,
        )
    )
    ci_map: dict[tuple[int, int], float] = {}
    for ci in ci_result.scalars().all():
        if ci.composite_index is not None:
            ci_map[(ci.race_id, ci.horse_id)] = float(ci.composite_index)

    # オッズ取得（最新）
    # OddsHistory.combination = 馬番文字列（例: "3"）、OddsHistory.odds = 倍率
    from ..db.models import OddsHistory  # 循環避けローカルインポート
    odds_result = await db.execute(
        select(OddsHistory).where(
            OddsHistory.race_id.in_(race_ids),
            OddsHistory.bet_type.in_(["win", "place"]),
        ).order_by(OddsHistory.fetched_at.desc())
    )
    # 最新オッズのみ保持 (race_id, combination) → odds
    win_odds_map: dict[tuple[int, str], float] = {}
    place_odds_map: dict[tuple[int, str], float] = {}
    for oh in odds_result.scalars().all():
        combo = oh.combination  # "3" 等の馬番文字列
        if oh.bet_type == "win" and (oh.race_id, combo) not in win_odds_map:
            win_odds_map[(oh.race_id, combo)] = float(oh.odds) if oh.odds is not None else 0.0
        elif oh.bet_type == "place" and (oh.race_id, combo) not in place_odds_map:
            place_odds_map[(oh.race_id, combo)] = float(oh.odds) if oh.odds is not None else 0.0

    # 着順取得
    results_result = await db.execute(
        select(RaceResult).where(RaceResult.race_id.in_(race_ids))
    )
    finish_map: dict[tuple[int, int], int | None] = {}
    for rr in results_result.scalars().all():
        if rr.horse_number is not None:
            finish_map[(rr.race_id, rr.horse_number)] = rr.finish_position

    # 表示設定に基づく他ユーザー取得
    disp_result = await db.execute(
        select(UserDisplaySetting, User)
        .join(User, UserDisplaySetting.target_user_id == User.id)
        .where(
            UserDisplaySetting.owner_user_id == me.id,
            UserDisplaySetting.show_mark.is_(True),
            User.is_active.is_(True),
        )
    )
    display_targets = list(disp_result.all())

    # 他ユーザーの予想取得
    other_user_ids = [u.id for _, u in display_targets]
    other_preds_map: dict[int, dict[tuple[int, int], UserPrediction]] = {}
    if other_user_ids:
        other_preds_result = await db.execute(
            select(UserPrediction).where(
                UserPrediction.user_id.in_(other_user_ids),
                UserPrediction.race_id.in_(race_ids),
            )
        )
        for p in other_preds_result.scalars().all():
            other_preds_map.setdefault(p.user_id, {})[(p.race_id, p.horse_id)] = p

    # レース別集計
    output: list[RacePredictionOut] = []
    for race in races:
        race_entries = entries_by_race.get(race.id, [])

        # 自分の指数合計（占有率計算用）
        total_index: float = 0.0
        for e, _ in race_entries:
            pred_for_sum = my_preds.get((race.id, e.horse_id))
            if pred_for_sum is not None and pred_for_sum.user_index is not None:
                total_index += float(pred_for_sum.user_index)

        horses_out: list[PredictionOut] = []
        for entry, horse in race_entries:
            pred = my_preds.get((race.id, entry.horse_id))
            ui = float(pred.user_index) if pred and pred.user_index is not None else None
            share = (ui / total_index) if (ui is not None and total_index > 0) else None

            horses_out.append(PredictionOut(
                horse_id=horse.id,
                horse_number=entry.horse_number,
                horse_name=horse.name,
                frame_number=entry.frame_number,
                mark=pred.mark if pred else None,
                user_index=ui,
                index_share=round(share, 4) if share is not None else None,
                galloplab_index=ci_map.get((race.id, horse.id)),
                win_odds=win_odds_map.get((race.id, str(entry.horse_number))),
                place_odds=place_odds_map.get((race.id, str(entry.horse_number))),
                finish_position=finish_map.get((race.id, entry.horse_number)),
            ))

        # 他ユーザー予想
        other_users_out: list[OtherUserPredictionOut] = []
        for disp_setting, target_user in display_targets:
            show_idx = disp_setting.show_index and target_user.can_input_index
            user_preds = other_preds_map.get(target_user.id, {})
            other_horse_preds: list[OtherHorsePrediction] = []
            for entry, horse in race_entries:
                other_pred = user_preds.get((race.id, entry.horse_id))
                if other_pred is None:
                    continue
                other_horse_preds.append(OtherHorsePrediction(
                    horse_id=horse.id,
                    mark=other_pred.mark,
                    user_index=float(other_pred.user_index) if (show_idx and other_pred.user_index is not None) else None,
                ))
            if other_horse_preds:
                other_users_out.append(OtherUserPredictionOut(
                    user_id=target_user.id,
                    user_name=target_user.name,
                    show_index=show_idx,
                    predictions=other_horse_preds,
                ))

        output.append(RacePredictionOut(
            race_id=race.id,
            race_name=race.race_name,
            race_number=race.race_number,
            course_name=race.course_name,
            horses=horses_out,
            other_users=other_users_out,
        ))

    return output


# ---------------------------------------------------------------------------
# エンドポイント: 印・指数保存（upsert）
# ---------------------------------------------------------------------------
@router.post("/predictions", status_code=status.HTTP_200_OK)
async def upsert_prediction(
    body: PredictionIn,
    x_user_email: Annotated[str, Query()],
    db: DbDep,
) -> dict[str, Any]:
    """印・指数を保存する（既存データは上書き）。

    mark=None かつ user_index=None の場合はレコードを削除する。
    """
    me = await _get_user_by_email(x_user_email, db)

    # 入力バリデーション
    if body.mark is not None and body.mark not in VALID_MARKS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"mark は {'、'.join(VALID_MARKS)} のいずれかを指定してください",
        )
    if body.user_index is not None and not me.can_input_index:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="指数投入権限がありません",
        )

    # mark も user_index も None → 削除
    if body.mark is None and body.user_index is None:
        existing = await db.execute(
            select(UserPrediction).where(
                UserPrediction.user_id == me.id,
                UserPrediction.race_id == body.race_id,
                UserPrediction.horse_id == body.horse_id,
            )
        )
        pred = existing.scalar_one_or_none()
        if pred:
            await db.delete(pred)
            await db.commit()
        return {"ok": True, "action": "deleted"}

    now = datetime.now(UTC)
    stmt = (
        pg_insert(UserPrediction)
        .values(
            user_id=me.id,
            race_id=body.race_id,
            horse_id=body.horse_id,
            mark=body.mark,
            user_index=Decimal(str(body.user_index)) if body.user_index is not None else None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_user_predictions_key",
            set_={
                "mark": body.mark,
                "user_index": Decimal(str(body.user_index)) if body.user_index is not None else None,
                "updated_at": now,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return {"ok": True, "action": "upserted"}


# ---------------------------------------------------------------------------
# エンドポイント: TARGET CSVファイル投入
# ---------------------------------------------------------------------------
@router.post("/import", response_model=ImportLogOut)
async def import_target_csv(
    x_user_email: Annotated[str, Query()],
    db: DbDep,
    file: UploadFile = File(...),
) -> ImportLogOut:
    """TARGET外部指数CSVを取り込む（仮フォーマット v1）。

    CSVフォーマット（ヘッダー行必須）:
        race_date,course_code,race_no,horse_no,index

    - race_date: YYYYMMDD
    - course_code: JRA2桁（例: 05=東京）
    - race_no: レース番号（1〜12）
    - horse_no: 馬番
    - index: 指数値（数値）
    """
    me = await _get_user_by_email(x_user_email, db)
    if not me.can_input_index:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="指数投入権限がありません",
        )

    content = await file.read()
    text = content.decode("utf-8-sig")  # BOM対応
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None or not TARGET_CSV_REQUIRED_COLS.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"CSVヘッダーに必要な列がありません: {TARGET_CSV_REQUIRED_COLS}",
        )

    rows = list(reader)
    total = len(rows)
    saved = 0
    errors = 0
    race_dates: set[str] = set()
    now = datetime.now(UTC)

    for row in rows:
        try:
            race_date = row["race_date"].strip()
            course_code = row["course_code"].strip().zfill(2)
            race_no = int(row["race_no"].strip())
            horse_no = int(row["horse_no"].strip())
            index_val = Decimal(row["index"].strip())
            race_dates.add(race_date)

            # jravan_race_id の kai/day は不明なので race_date + course + race_no で一意に特定
            # races テーブルは date + course_code（2桁）+ race_number で絞り込む
            race_result = await db.execute(
                select(Race).where(
                    Race.date == race_date,
                    Race.jravan_race_id.like(f"{race_date}{course_code}%"),
                    Race.race_number == race_no,
                )
            )
            race = race_result.scalars().first()
            if race is None:
                logger.debug("Race not found: date=%s course=%s no=%d", race_date, course_code, race_no)
                errors += 1
                continue

            # 馬番から horse_id を取得
            entry_result = await db.execute(
                select(RaceEntry).where(
                    RaceEntry.race_id == race.id,
                    RaceEntry.horse_number == horse_no,
                )
            )
            entry = entry_result.scalar_one_or_none()
            if entry is None:
                errors += 1
                continue

            stmt = (
                pg_insert(UserPrediction)
                .values(
                    user_id=me.id,
                    race_id=race.id,
                    horse_id=entry.horse_id,
                    user_index=index_val,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_user_predictions_key",
                    set_={"user_index": index_val, "updated_at": now},
                )
            )
            await db.execute(stmt)
            saved += 1

        except Exception as e:
            logger.warning("CSV row parse error: %s – %s", row, e)
            errors += 1

    await db.flush()

    # 投入ログ記録
    import_log = UserImport(
        user_id=me.id,
        filename=file.filename or "unknown.csv",
        race_date=", ".join(sorted(race_dates)) if race_dates else "",
        total_count=total,
        saved_count=saved,
        error_count=errors,
    )
    db.add(import_log)
    await db.commit()
    await db.refresh(import_log)

    return ImportLogOut(
        id=import_log.id,
        filename=import_log.filename,
        race_date=import_log.race_date,
        total_count=import_log.total_count,
        saved_count=import_log.saved_count,
        error_count=import_log.error_count,
        created_at=import_log.created_at,
    )


@router.get("/import/history", response_model=list[ImportLogOut])
async def get_import_history(
    x_user_email: Annotated[str, Query()],
    db: DbDep,
    limit: int = Query(20, ge=1, le=100),
) -> list[ImportLogOut]:
    """自分のファイル投入履歴を返す。"""
    me = await _get_user_by_email(x_user_email, db)
    result = await db.execute(
        select(UserImport)
        .where(UserImport.user_id == me.id)
        .order_by(UserImport.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        ImportLogOut(
            id=lg.id,
            filename=lg.filename,
            race_date=lg.race_date,
            total_count=lg.total_count,
            saved_count=lg.saved_count,
            error_count=lg.error_count,
            created_at=lg.created_at,
        )
        for lg in logs
    ]


# ---------------------------------------------------------------------------
# エンドポイント: 成績集計
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=StatsOut)
async def get_stats(
    x_user_email: Annotated[str, Query()],
    db: DbDep,
    from_date: str | None = Query(None, description="YYYYMMDD"),
    to_date: str | None = Query(None, description="YYYYMMDD"),
    course: str | None = Query(None, description="競馬場名（部分一致）"),
    surface: str | None = Query(None, description="芝/ダート"),
    dist_min: int | None = Query(None),
    dist_max: int | None = Query(None),
) -> StatsOut:
    """印別・指数帯別・占有率帯別の成績集計を返す。"""
    me = await _get_user_by_email(x_user_email, db)

    # 基本条件: 着順が確定しているレース
    conditions = [
        RaceResult.finish_position.is_not(None),
        UserPrediction.user_id == me.id,
    ]
    if from_date:
        conditions.append(Race.date >= from_date)
    if to_date:
        conditions.append(Race.date <= to_date)
    if course:
        conditions.append(Race.course_name.ilike(f"%{course}%"))
    if surface:
        conditions.append(Race.surface == surface)
    if dist_min is not None:
        conditions.append(Race.distance >= dist_min)
    if dist_max is not None:
        conditions.append(Race.distance <= dist_max)

    # ベースクエリ: 自分の予想 × レース結果 × 払戻
    base_q = (
        select(
            UserPrediction.mark,
            UserPrediction.user_index,
            RaceResult.finish_position,
            RaceResult.horse_number,
            Race.id.label("race_id"),
        )
        .join(Race, UserPrediction.race_id == Race.id)
        .join(
            RaceResult,
            and_(
                RaceResult.race_id == UserPrediction.race_id,
                RaceResult.horse_number == select(RaceEntry.horse_number)
                    .where(RaceEntry.race_id == UserPrediction.race_id)
                    .where(RaceEntry.horse_id == UserPrediction.horse_id)
                    .scalar_subquery(),
            ),
        )
        .where(*conditions)
    )
    rows_result = await db.execute(base_q)
    rows = rows_result.all()

    if not rows:
        return StatsOut(by_mark=[], by_index_range=[], by_share_range=[])

    # 払戻取得（単勝・複勝）
    race_ids_set = {r.race_id for r in rows}
    payouts_result = await db.execute(
        select(RacePayout).where(
            RacePayout.race_id.in_(race_ids_set),
            RacePayout.bet_type.in_(["win", "place"]),
        )
    )
    # payout_map: (race_id, combination文字列, bet_type) → 払戻金額（100円あたり÷100でオッズ換算）
    payout_map: dict[tuple[int | None, str, str], float] = {}
    for p in payouts_result.scalars().all():
        payout_map[(p.race_id, p.combination, p.bet_type)] = p.payout / 100.0

    # 同レース全馬の指数合計（占有率計算用）
    all_preds_result = await db.execute(
        select(UserPrediction.race_id, func.sum(UserPrediction.user_index).label("total_index"))
        .where(
            UserPrediction.user_id == me.id,
            UserPrediction.race_id.in_(race_ids_set),
            UserPrediction.user_index.is_not(None),
        )
        .group_by(UserPrediction.race_id)
    )
    race_total_index: dict[int, float] = {
        r.race_id: float(r.total_index) for r in all_preds_result.all()
    }

    # 集計データ構築
    def _calc_stats(subset: list) -> dict[str, Any]:
        n = len(subset)
        if n == 0:
            return {"count": 0, "win_rate": 0.0, "place_rate": 0.0, "win_roi": 0.0, "place_roi": 0.0}
        win_cnt = sum(1 for r in subset if r.finish_position == 1)
        place_cnt = sum(1 for r in subset if r.finish_position is not None and r.finish_position <= 3)
        win_payout = sum(
            payout_map.get((r.race_id, str(r.horse_number), "win"), 0.0)
            for r in subset if r.finish_position == 1
        )
        place_payout = sum(
            payout_map.get((r.race_id, str(r.horse_number), "place"), 0.0)
            for r in subset if r.finish_position is not None and r.finish_position <= 3
        )
        return {
            "count": n,
            "win_rate": round(win_cnt / n, 4),
            "place_rate": round(place_cnt / n, 4),
            "win_roi": round(win_payout / n, 4),
            "place_roi": round(place_payout / n, 4),
        }

    # 印別
    by_mark_map: dict[str, list] = {}
    for r in rows:
        if r.mark:
            by_mark_map.setdefault(r.mark, []).append(r)
    mark_order = ["◎", "○", "▲", "△", "×"]
    by_mark = [
        MarkStats(mark=m, win_count=sum(1 for r in by_mark_map[m] if r.finish_position == 1),
                  place_count=sum(1 for r in by_mark_map[m] if r.finish_position is not None and r.finish_position <= 3),
                  **_calc_stats(by_mark_map[m]))
        for m in mark_order if m in by_mark_map
    ]

    # 指数帯別
    index_ranges = [
        ("〜59", 0.0, 60.0),
        ("60〜69", 60.0, 70.0),
        ("70〜79", 70.0, 80.0),
        ("80〜89", 80.0, 90.0),
        ("90〜", 90.0, None),
    ]
    by_index_range: list[IndexRangeStats] = []
    for label, lo, hi in index_ranges:
        subset = [
            r for r in rows
            if r.user_index is not None
            and float(r.user_index) >= lo
            and (hi is None or float(r.user_index) < hi)
        ]
        stats = _calc_stats(subset)
        by_index_range.append(IndexRangeStats(label=label, min_val=lo, max_val=hi, **stats))

    # 占有率帯別
    share_ranges = [
        ("〜10%", 0.0, 0.10),
        ("10%〜15%", 0.10, 0.15),
        ("15%〜20%", 0.15, 0.20),
        ("20%〜25%", 0.20, 0.25),
        ("25%〜", 0.25, None),
    ]
    by_share_range: list[ShareRangeStats] = []
    for label, lo, hi in share_ranges:
        subset = [
            r for r in rows
            if r.user_index is not None
            and race_total_index.get(r.race_id, 0) > 0
            and float(r.user_index) / race_total_index[r.race_id] >= lo
            and (hi is None or float(r.user_index) / race_total_index[r.race_id] < hi)
        ]
        stats = _calc_stats(subset)
        by_share_range.append(ShareRangeStats(label=label, min_val=lo, max_val=hi, **stats))

    return StatsOut(by_mark=by_mark, by_index_range=by_index_range, by_share_range=by_share_range)


# ---------------------------------------------------------------------------
# エンドポイント: 表示設定
# ---------------------------------------------------------------------------
@router.get("/settings/display", response_model=list[DisplaySettingOut])
async def get_display_settings(
    x_user_email: Annotated[str, Query()],
    db: DbDep,
) -> list[DisplaySettingOut]:
    """自分の他ユーザー表示設定一覧を返す。全有効ユーザーを返し、未設定はデフォルト（show_mark=True, show_index=False）。"""
    me = await _get_user_by_email(x_user_email, db)

    # 全有効ユーザー（自分以外）
    all_users_result = await db.execute(
        select(User).where(User.is_active.is_(True), User.id != me.id).order_by(User.name)
    )
    all_users = all_users_result.scalars().all()

    # 既存設定
    settings_result = await db.execute(
        select(UserDisplaySetting).where(UserDisplaySetting.owner_user_id == me.id)
    )
    settings_map: dict[int, UserDisplaySetting] = {
        s.target_user_id: s for s in settings_result.scalars().all()
    }

    output: list[DisplaySettingOut] = []
    for u in all_users:
        s = settings_map.get(u.id)
        output.append(DisplaySettingOut(
            target_user_id=u.id,
            target_user_name=u.name,
            target_user_email=u.email,
            target_can_input_index=u.can_input_index,
            show_mark=s.show_mark if s else True,
            show_index=s.show_index if s else False,
        ))
    return output


@router.put("/settings/display", status_code=status.HTTP_200_OK)
async def update_display_settings(
    body: list[DisplaySettingIn],
    x_user_email: Annotated[str, Query()],
    db: DbDep,
) -> dict[str, Any]:
    """表示設定を一括更新する（upsert）。"""
    me = await _get_user_by_email(x_user_email, db)
    now = datetime.now(UTC)

    for item in body:
        if item.target_user_id == me.id:
            continue
        stmt = (
            pg_insert(UserDisplaySetting)
            .values(
                owner_user_id=me.id,
                target_user_id=item.target_user_id,
                show_mark=item.show_mark,
                show_index=item.show_index,
                created_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_user_display_settings_key",
                set_={"show_mark": item.show_mark, "show_index": item.show_index},
            )
        )
        await db.execute(stmt)

    await db.commit()
    return {"ok": True, "updated": len(body)}
