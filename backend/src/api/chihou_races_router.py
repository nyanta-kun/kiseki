"""地方競馬 レース参照APIルーター"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouHorse,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRaceResult,
)
from ..db.session import get_db
from ..indices.chihou_calculator import BANEI_COURSE_CODE, CHIHOU_COMPOSITE_VERSION
from ..indices.chihou_cutoff import cut_flags
from ..indices.chihou_gekisou import STATUS_GEKISOU, STATUS_MIOKURI, GekisouVerdict
from ..indices.confidence import (
    CHIHOU_DISPERSION_FULL_SCORE,
    CHIHOU_GAP_FULL_SCORE,
    calculate_race_confidence,
)
from ..services.chihou_gekisou_source import fetch_gekisou_verdicts
from ..services.chihou_odds_freshness import (
    STATUS_MISSING,
    OddsFreshness,
    classify_odds_freshness,
    post_time_to_utc,
)
from ..services.chihou_odds_query import latest_odds_sql
from ..utils.constants import CHIHOU_INDEX_DISPLAY_ADJUST
from .ws_manager import chihou_results_manager


def _adj_chihou(v: float | None, key: str) -> float | None:
    """地方競馬 個別指数の表示用バイアス補正。

    式: display = (raw + offset - 50) * scale + 50  → clip(0, 100)
    composite_index は重み校正済みのため補正しない。
    """
    if v is None:
        return None
    offset, scale = CHIHOU_INDEX_DISPLAY_ADJUST.get(key, (0.0, 1.0))
    adjusted = (float(v) + offset - 50.0) * scale + 50.0
    return round(max(0.0, min(100.0, adjusted)), 1)


router = APIRouter(prefix="/api/chihou/races", tags=["chihou-races"])
DbDep = Annotated[AsyncSession, Depends(get_db)]


class ChihouRaceOut(BaseModel):
    """地方競馬レース情報レスポンス（JRA Race 型互換）"""

    id: int
    date: str
    course_name: str
    race_number: int
    race_name: str | None = None
    surface: str
    distance: int
    grade: str | None = None
    condition: str | None = None
    weather: str | None = None
    head_count: int | None = None
    post_time: str | None = None
    race_class_label: str | None = None
    has_indices: bool = False
    has_anagusa: bool = False
    confidence_score: int | None = None
    confidence_label: str | None = None
    confidence_rank: str | None = None  # S / A / B / C
    result_confirmed: bool = False  # 成績確定済み（JRA RaceOut と互換）
    # 激走 / 見送り（`indices/chihou_gekisou.py`）。"gekisou" | "miokuri" | None
    # 🔴 2026-09-28 に 購入指針(buy_signal) / 期待値ランク(recommend_rank) /
    #    注目馬★(has_place_pick) を撤去してこれに置き換えた。いずれも回収率の根拠が無かった。
    gekisou_status: str | None = None

    model_config = {"from_attributes": True}


class ChihouHorseIndexOut(BaseModel):
    """地方競馬 馬指数レスポンス"""

    horse_id: int
    horse_number: int | None = None
    horse_name: str
    composite_index: float
    win_probability: float | None = None
    place_probability: float | None = None
    speed_index: float | None = None
    last3f_index: float | None = None
    jockey_index: float | None = None
    rotation_index: float | None = None
    last_margin_index: float | None = None  # 前走着差指数（0-100, 接戦=高評価, v5以降）
    place_ev_index: float | None = None  # 複勝期待値指数（EV>1.0→50超、v3以降）
    external_consensus: int | None = None  # 0〜2: kichiuma/netkeibaで1位になった数
    win_odds: float | None = None  # 単勝オッズ（最新）
    # 激走馬（1レース最大1頭）。判定は `indices/chihou_gekisou.py`。
    # 🔴 2026-09-28 に スイートスポット(赤字) / 複穴 / 注目馬★ / EV を撤去して置き換えた。
    is_gekisou: bool = False
    # 足切り候補（Web でグレーアウト表示する馬）。ルールの正本は
    # `src/indices/chihou_cutoff.py`（2026-09-06 に frontend から移設）。
    is_cut_off: bool = False


class ChihouRaceRanks(BaseModel):
    """地方競馬 レース信頼度ランク（指数の分離度。期待値ではない）"""

    score: int
    confidence_rank: str  # S / A / B / C
    gap_1_2: float
    gap_1_3: float


class ChihouGekisouOut(BaseModel):
    """地方競馬 激走 / 見送り判定（レース単位）。

    `status` が None のレースは「印なし」（空き枠はあるが押しのけそうな人気薄がいない）。
    """

    status: str | None  # "gekisou" | "miokuri" | None
    source: str  # "snapshot"（発走前記録で確定）| "live"（暫定）
    room: float | None = None  # 空き枠 = 3 − 人気1〜3番の好走見込み
    horse_number: int | None = None  # 激走馬
    popularity: int | None = None  # 激走馬の人気（判定に使った発走前オッズ）
    place_prob: float | None = None  # 激走馬が複勝圏に入る確率（較正済み）


class ChihouIndicesResponse(BaseModel):
    """地方競馬 指数レスポンス"""

    horses: list[ChihouHorseIndexOut]
    ranks: ChihouRaceRanks | None = None
    gekisou: ChihouGekisouOut | None = None


def _gekisou_out(verdict: GekisouVerdict, source: str) -> ChihouGekisouOut:
    return ChihouGekisouOut(
        status=verdict.status,
        source=source,
        room=round(verdict.room, 3) if verdict.room is not None else None,
        horse_number=verdict.pick,
        popularity=verdict.pick_pop,
        place_prob=_round_prob(verdict.pick_place_prob),
    )


def _round_prob(p: float | None) -> float | None:
    return round(p, 4) if p is not None else None


class ChihouResultOut(BaseModel):
    """地方競馬 成績レスポンス（JRA RaceResult 型互換）"""

    horse_number: int | None = None
    finish_position: int | None = None
    finish_time: float | None = None
    last_3f: float | None = None
    horse_name: str


class ChihouTopHorseOut(BaseModel):
    course_name: str
    race_number: int
    race_name: str | None
    post_time: str | None
    horse_number: int | None
    horse_name: str | None
    win_probability: float
    win_odds: float | None
    finish_position: int | None


@router.get("/top-probability")
async def get_chihou_top_probability(
    date: str = Query(..., description="開催日 YYYYMMDD"),
    threshold: float = Query(0.5, description="勝率閾値（デフォルト0.5）"),
    db: AsyncSession = Depends(get_db),
) -> list[ChihouTopHorseOut]:
    """指定日の勝率閾値以上の馬を発走時刻順で返す。"""
    from sqlalchemy import text as _text

    sql = _text("""
        SELECT
            r.course_name,
            r.race_number,
            r.race_name,
            r.post_time,
            re.horse_number,
            h.name AS horse_name,
            ci.win_probability,
            oh.odds AS win_odds,
            rr.finish_position
        FROM chihou.races r
        JOIN chihou.race_entries re ON re.race_id = r.id
        JOIN chihou.horses h ON h.id = re.horse_id
        JOIN LATERAL (
            SELECT win_probability FROM chihou.calculated_indices
            WHERE race_id = r.id AND horse_id = re.horse_id
            ORDER BY version DESC, calculated_at DESC
            LIMIT 1
        ) ci ON TRUE
        LEFT JOIN LATERAL (
            SELECT odds FROM chihou.odds_history
            WHERE race_id = r.id
              AND bet_type = 'win'
              AND combination = re.horse_number::text
              -- 発走時刻以前に限定（発走後もオッズ取得は続くため。chihou_odds_query と同じ規約）
              AND (
                r.post_time !~ '^[0-9]{4}$'
                OR fetched_at <= (
                    to_timestamp(r.date || r.post_time, 'YYYYMMDDHH24MI') - interval '9 hours'
                )
              )
            ORDER BY fetched_at DESC
            LIMIT 1
        ) oh ON TRUE
        LEFT JOIN chihou.race_results rr
            ON rr.race_id = r.id AND rr.horse_id = re.horse_id
        WHERE r.date = :date
          AND ci.win_probability >= :threshold
          AND (oh.odds IS NULL OR oh.odds >= 2.0)
        ORDER BY r.post_time ASC NULLS LAST, r.race_number ASC
    """)
    result = await db.execute(sql, {"date": date, "threshold": threshold})
    rows = result.all()
    return [
        ChihouTopHorseOut(
            course_name=r[0],
            race_number=r[1],
            race_name=r[2],
            post_time=r[3],
            horse_number=r[4],
            horse_name=r[5],
            win_probability=float(r[6]),
            win_odds=float(r[7]) if r[7] is not None else None,
            finish_position=r[8],
        )
        for r in rows
    ]


class ChihouGekisouPickOut(BaseModel):
    """地方競馬 激走馬 1頭ぶん（推奨タブの一覧用）。"""

    race_id: int
    course_name: str
    race_number: int
    race_name: str | None
    post_time: str | None
    horse_number: int
    horse_name: str | None
    popularity: int | None  # 判定に使った発走前オッズでの人気
    # 複勝圏に入る確率（較正済み・`chihou_gekisou.gekisou_place_prob`）。
    # ⚠️ 確率が高いほど回収率が高いわけではない（前向き記録でどの帯も 0.68〜0.91）
    place_prob: float | None
    win_odds: float | None  # 判定に使った単勝オッズ
    place_odds: float | None  # 判定時点の複勝オッズ（下限）
    room: float | None  # 空き枠
    source: str  # "snapshot"（確定）| "live"（暫定・発走前はまだ動く）
    finish_position: int | None
    place_payout: float | None  # 複勝払戻（倍率）。複勝圏外・未確定は None


class ChihouGekisouDayOut(BaseModel):
    """地方競馬 激走 / 見送りの当日まとめ。"""

    picks: list[ChihouGekisouPickOut]
    n_judged: int  # 判定できたレース（8頭以上・指数とオッズあり）
    n_gekisou: int
    n_miokuri: int


@router.get("/gekisou")
async def get_chihou_gekisou(
    date: str = Query(..., description="開催日 YYYYMMDD"),
    db: AsyncSession = Depends(get_db),
) -> ChihouGekisouDayOut:
    """指定日の「激走」馬を発走時刻順で返す。

    判定は `indices/chihou_gekisou.judge_gekisou`、入力の選び方（前向き記録が
    あればそれが正本）は `services/chihou_gekisou_source` を参照。

    ⚠️ 回収率は 1.0 に届かない（前向き確認 0.790）。当たりやすさの印であって
    期待値の印ではない。
    """
    races_result = await db.execute(
        select(ChihouRace)
        .where(ChihouRace.date == date)
        .where(ChihouRace.course != BANEI_COURSE_CODE)
        .order_by(ChihouRace.post_time.asc().nullslast(), ChihouRace.race_number)
    )
    races = list(races_result.scalars().all())
    if not races:
        return ChihouGekisouDayOut(picks=[], n_judged=0, n_gekisou=0, n_miokuri=0)
    race_ids = [r.id for r in races]
    verdicts = await fetch_gekisou_verdicts(db, race_ids)

    name_rows = await db.execute(
        select(ChihouRaceEntry.race_id, ChihouRaceEntry.horse_number, ChihouHorse.name)
        .join(ChihouHorse, ChihouHorse.id == ChihouRaceEntry.horse_id)
        .where(ChihouRaceEntry.race_id.in_(race_ids))
    )
    name_map = {(int(r), int(h)): n for r, h, n in name_rows.all() if h is not None}
    result_rows = await db.execute(
        select(
            ChihouRaceResult.race_id,
            ChihouRaceResult.horse_number,
            ChihouRaceResult.finish_position,
        ).where(ChihouRaceResult.race_id.in_(race_ids))
    )
    finish_map = {(int(r), int(h)): fp for r, h, fp in result_rows.all() if h is not None}
    pay_rows = await db.execute(
        sql_text(
            "SELECT race_id, combination, payout FROM chihou.race_payouts"
            " WHERE race_id = ANY(:race_ids) AND bet_type = 'place'"
        ),
        {"race_ids": race_ids},
    )
    pay_map = {(int(r), int(c)): p / 100.0 for r, c, p in pay_rows.all() if str(c).isdigit() and p is not None}

    picks: list[ChihouGekisouPickOut] = []
    n_judged = n_gekisou = n_miokuri = 0
    for race in races:
        sv = verdicts.get(race.id)
        if sv is None or sv.verdict.room is None:
            continue
        n_judged += 1
        v = sv.verdict
        if v.status == STATUS_MIOKURI:
            n_miokuri += 1
        if v.status != STATUS_GEKISOU or v.pick is None:
            continue
        n_gekisou += 1
        key = (race.id, v.pick)
        picks.append(
            ChihouGekisouPickOut(
                race_id=race.id,
                course_name=race.course_name,
                race_number=race.race_number,
                race_name=race.race_name,
                post_time=race.post_time,
                horse_number=v.pick,
                horse_name=name_map.get(key),
                popularity=v.pick_pop,
                place_prob=_round_prob(v.pick_place_prob),
                win_odds=sv.win_odds.get(v.pick),
                place_odds=sv.place_odds.get(v.pick),
                room=round(v.room, 3) if v.room is not None else None,
                source=sv.source,
                finish_position=finish_map.get(key),
                place_payout=pay_map.get(key),
            )
        )
    return ChihouGekisouDayOut(picks=picks, n_judged=n_judged, n_gekisou=n_gekisou, n_miokuri=n_miokuri)


@router.get("/race-keys")
async def get_chihou_race_keys(
    date: str = Query(..., description="開催日 YYYYMMDD"),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """指定日の地方競馬レースキー（umaconn_race_id）一覧を返す。UmaConnエージェント用。

    post_time もあわせて返す。realtimeループが発走済みのレースだけに0B12（速報成績）
    問い合わせを絞り込むために使う（未発走レースへの無駄なポーリングを避け、
    確定直後のレースをより早く検知するため）。
    """
    result = await db.execute(
        select(ChihouRace.id, ChihouRace.umaconn_race_id, ChihouRace.post_time)
        .where(ChihouRace.date == date)
        .where(ChihouRace.umaconn_race_id.isnot(None))
        .where(ChihouRace.course != BANEI_COURSE_CODE)
        .order_by(ChihouRace.race_number)
    )
    rows = result.all()
    return [{"id": row[0], "race_key": row[1], "post_time": row[2]} for row in rows]


@router.get("")
async def get_chihou_races_by_date(
    date: str = Query(..., description="開催日 YYYYMMDD"),
    db: AsyncSession = Depends(get_db),
) -> list[ChihouRaceOut]:
    """指定日の地方競馬レース一覧を返す。"""
    result = await db.execute(
        select(ChihouRace)
        .where(ChihouRace.date == date)
        .where(ChihouRace.course != BANEI_COURSE_CODE)
        .order_by(ChihouRace.race_number)
    )
    races = result.scalars().all()

    if not races:
        return []

    race_ids = [r.id for r in races]

    # --- 指数の有無 ---
    idx_rows = await db.execute(
        select(ChihouCalculatedIndex.race_id)
        .where(ChihouCalculatedIndex.race_id.in_(race_ids))
        .where(ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION)
        .distinct()
    )
    indexed_race_ids = {int(rid) for (rid,) in idx_rows.all()}

    # --- 成績確定レース取得（finish_position が存在するレース）---
    confirmed_rows = await db.execute(
        select(ChihouRaceResult.race_id)
        .where(ChihouRaceResult.race_id.in_(race_ids))
        .where(ChihouRaceResult.finish_position.isnot(None))
        .distinct()
    )
    confirmed_race_ids: set[int] = {r[0] for r in confirmed_rows.all()}

    # --- 激走 / 見送り（推奨タブ・レース詳細と同じ判定・同じ入力の優先順位） ---
    # 🔴 2026-09-28 に 信頼度/期待値ランク・購入指針・注目馬★ の算出をここから撤去した
    #    （一覧で表示しなくなったため。回収率の根拠が無かった）。
    verdicts = await fetch_gekisou_verdicts(db, race_ids)

    return [
        ChihouRaceOut(
            id=race.id,
            date=race.date,
            course_name=race.course_name,
            race_number=race.race_number,
            race_name=race.race_name,
            surface=race.surface,
            distance=race.distance,
            grade=race.grade,
            condition=race.condition,
            weather=race.weather,
            head_count=race.head_count,
            post_time=race.post_time,
            has_indices=race.id in indexed_race_ids,
            result_confirmed=race.id in confirmed_race_ids,
            gekisou_status=verdicts[race.id].verdict.status if race.id in verdicts else None,
        )
        for race in races
    ]


@router.get("/nearest-date")
async def get_chihou_nearest_date(
    from_: str = Query(..., alias="from", description="基準日 YYYYMMDD"),
    direction: str = Query(..., description="prev または next"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """前後の地方競馬開催日を返す。"""
    if direction == "prev":
        result = await db.execute(
            select(ChihouRace.date)
            .where(ChihouRace.date < from_)
            .where(ChihouRace.course != BANEI_COURSE_CODE)
            .order_by(ChihouRace.date.desc())
            .limit(1)
        )
    else:
        result = await db.execute(
            select(ChihouRace.date)
            .where(ChihouRace.date > from_)
            .where(ChihouRace.course != BANEI_COURSE_CODE)
            .order_by(ChihouRace.date.asc())
            .limit(1)
        )

    row = result.scalar()
    if not row:
        raise HTTPException(status_code=404, detail="No date found")

    return {"date": row}


@router.get("/{race_id}/indices")
async def get_chihou_race_indices(race_id: int, db: DbDep) -> ChihouIndicesResponse:
    """レースの指数一覧を返す。"""
    result = await db.execute(
        select(
            ChihouCalculatedIndex,
            ChihouHorse.name.label("horse_name"),
            ChihouRaceEntry.horse_number,
        )
        .join(ChihouHorse, ChihouCalculatedIndex.horse_id == ChihouHorse.id)
        .outerjoin(
            ChihouRaceEntry,
            (ChihouRaceEntry.race_id == ChihouCalculatedIndex.race_id)
            & (ChihouRaceEntry.horse_id == ChihouCalculatedIndex.horse_id),
        )
        .where(ChihouCalculatedIndex.race_id == race_id)
        .where(ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION)
        .order_by(ChihouCalculatedIndex.composite_index.desc())
    )
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No indices found for this race")

    # 外部指数コンセンサス取得（sekito.kichiuma / sekito.netkeiba）
    ext_sql = sql_text("""
        SELECT
            re.horse_number,
            k.sp_score,
            CASE
                WHEN n.idx_ave ~ '^-?[0-9]+\\*?$'
                THEN regexp_replace(n.idx_ave, '\\*', '')::float
                ELSE NULL
            END AS idx_ave
        FROM chihou.races r
        JOIN keiba.racecourse_map rc ON r.course = rc.netkeiba_id
        JOIN chihou.race_entries re ON re.race_id = r.id
        LEFT JOIN sekito.kichiuma k
            ON k.date = TO_DATE(r.date, 'YYYYMMDD')
            AND k.course_code = rc.code
            AND k.race_no = r.race_number
            AND k.horse_no = re.horse_number
        LEFT JOIN sekito.netkeiba n
            ON n.date = TO_DATE(r.date, 'YYYYMMDD')
            AND n.course_code = rc.code
            AND n.race_no = r.race_number
            AND n.horse_no = re.horse_number
            AND n.is_time_index = true
        WHERE r.id = :race_id
        ORDER BY re.horse_number
    """)
    ext_rows = (await db.execute(ext_sql, {"race_id": race_id})).fetchall()

    # 外部指数コンセンサス計算
    consensus_map: dict[int, int] = {}
    if ext_rows:
        # horse_number → (sp_score, idx_ave) の辞書
        ext_dict: dict[int, tuple[float | None, float | None]] = {
            r[0]: (float(r[1]) if r[1] is not None else None, float(r[2]) if r[2] is not None else None)
            for r in ext_rows
        }
        kichi_entries = [(hn, v[0]) for hn, v in ext_dict.items() if v[0] is not None]
        netk_entries = [(hn, v[1]) for hn, v in ext_dict.items() if v[1] is not None]
        kichi_top = max(kichi_entries, key=lambda x: x[1])[0] if kichi_entries else None
        netk_top = max(netk_entries, key=lambda x: x[1])[0] if netk_entries else None

        if kichi_top is not None or netk_top is not None:
            for hn in ext_dict:
                consensus_map[hn] = (1 if hn == kichi_top else 0) + (1 if hn == netk_top else 0)

    # --- 全馬の「発走時刻以前の」最新単勝オッズを一括取得 ---
    # 🔴 素の `ORDER BY fetched_at DESC` を使ってはいけない。オッズ取得は発走後も
    # 続くので、終了したレースを開くと**発走後のオッズ**で EV が出る（2026-08-24 発覚）。
    # 判定・表示とも `chihou_odds_query` の発走時刻フィルタ版が唯一の正本。
    odds_result = await db.execute(
        sql_text(latest_odds_sql(["win"])),
        {"race_ids": [race_id]},
    )
    win_odds_map: dict[str, float] = {
        str(combo): float(odds_val) for _rid, _bet_type, combo, odds_val in odds_result.all() if odds_val is not None
    }

    # --- レース情報（head_count）取得 ---
    race_row = await db.execute(select(ChihouRace).where(ChihouRace.id == race_id))
    race_obj = race_row.scalar_one_or_none()

    horses = []
    for row in rows:
        ci: ChihouCalculatedIndex = row[0]
        horse_name: str = row[1]
        horse_number: int | None = row[2]
        win_prob = float(ci.win_probability) if ci.win_probability is not None else None
        wo = win_odds_map.get(str(horse_number)) if horse_number is not None else None
        horses.append(
            ChihouHorseIndexOut(
                horse_id=ci.horse_id,
                horse_number=horse_number,
                horse_name=horse_name,
                composite_index=float(ci.composite_index) if ci.composite_index is not None else 0.0,
                win_probability=win_prob,
                place_probability=float(ci.place_probability) if ci.place_probability is not None else None,
                speed_index=float(ci.speed_index) if ci.speed_index is not None else None,
                last3f_index=float(ci.last3f_index) if ci.last3f_index is not None else None,
                jockey_index=_adj_chihou(
                    float(ci.jockey_index) if ci.jockey_index is not None else None, "jockey_index"
                ),
                rotation_index=_adj_chihou(
                    float(ci.rotation_index) if ci.rotation_index is not None else None, "rotation_index"
                ),
                last_margin_index=_adj_chihou(
                    float(ci.last_margin_index) if ci.last_margin_index is not None else None, "last_margin_index"
                ),
                place_ev_index=_adj_chihou(
                    float(ci.place_ev_index) if ci.place_ev_index is not None else None, "place_ev_index"
                ),
                external_consensus=consensus_map.get(horse_number)
                if (consensus_map and horse_number is not None)
                else None,
                win_odds=wo,
            )
        )

    # --- 激走 / 見送り（1レース最大1頭） ---
    # 🔴 2026-09-28 に スイートスポット(赤字) / 複穴 / 注目馬★ の個別馬バッジを撤去して
    #    置き換えた。いずれも回収率の根拠が無く、注目馬と激走は同じ人気薄を別の札で
    #    二重に出すことになるため。判定の入力（記録 or 最新オッズ）は一覧・推奨タブと同じ。
    gekisou: ChihouGekisouOut | None = None
    _sv = (await fetch_gekisou_verdicts(db, [race_id])).get(race_id)
    if _sv is not None:
        gekisou = _gekisou_out(_sv.verdict, _sv.source)
        if _sv.verdict.status == STATUS_GEKISOU:
            for h in horses:
                h.is_gekisou = h.horse_number == _sv.verdict.pick

    # --- 足切り（グレーアウト）判定 ---
    # 2026-09-06: ルールの正本を frontend から `indices/chihou_cutoff.py` へ移した。
    # 以前は frontend が composite_index から自前で gap と順位を作っており、
    # 閾値が backend の検証スクリプト2本と三重管理になっていた。
    for h, cut in zip(horses, cut_flags([h.composite_index for h in horses]), strict=True):
        h.is_cut_off = cut

    # --- 信頼度ランク算出（指数の分離度） ---
    ranks: ChihouRaceRanks | None = None
    if horses:
        ci_list = [h.composite_index for h in horses]
        wp_list = [h.win_probability for h in horses if h.win_probability is not None]

        conf = calculate_race_confidence(
            ci_list,
            race_obj.head_count if race_obj else None,
            wp_list or None,
            gap_full_score=CHIHOU_GAP_FULL_SCORE,
            dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE,
        )

        ranks = ChihouRaceRanks(
            score=conf["score"],
            confidence_rank=conf["rank"],
            gap_1_2=conf["gap_1_2"],
            gap_1_3=conf["gap_1_3"],
        )

    return ChihouIndicesResponse(horses=horses, ranks=ranks, gekisou=gekisou)


@router.get("/{race_id}/odds")
async def get_chihou_race_odds(race_id: int, db: DbDep) -> dict:
    """レースの最新単勝・複勝オッズを、**鮮度つきで**返す。

    odds_history から各馬の最新オッズを取得して返す。
    win/place の馬番→倍率 dict は JRA の `/races/{id}/odds` と同一スキーマで、
    `freshness` を足したものが地方版（JRA 側のクライアントは無視してよい）。

    🔴 **鮮度を必ず一緒に返すこと。** 取得が止まっても最後のスナップショットは
    DB に残るので、この API は 200 と「それらしい倍率」を返し続ける。
    2026-08-20 に取得が4時間51分止まったとき、画面には朝のオッズが最後まで
    出ていたが異常を示すものが何も無かった。値だけ返すのは危険。
    詳細は `services/chihou_odds_freshness.py`。
    """
    result = await db.execute(
        sql_text("""
            SELECT DISTINCT ON (bet_type, combination)
                bet_type, combination, odds
            FROM chihou.odds_history
            WHERE race_id = :rid
              AND bet_type IN ('win', 'place')
            ORDER BY bet_type, combination, fetched_at DESC
        """),
        {"rid": race_id},
    )
    rows = result.fetchall()
    win: dict[str, float] = {}
    place: dict[str, float] = {}
    for bet_type, combination, odds_val in rows:
        if odds_val is None:
            continue
        if bet_type == "win":
            win[combination] = float(odds_val)
        elif bet_type == "place":
            place[combination] = float(odds_val)

    freshness = await _fetch_odds_freshness(race_id, db)
    return {"win": win, "place": place, "freshness": freshness.to_dict()}


async def _fetch_odds_freshness(race_id: int, db: DbDep) -> OddsFreshness:
    """レースのオッズ鮮度を DB から引いて判定する。

    ⚠️ **時刻の扱いを SQL 側で完結させないこと。**
    `chihou.odds_history.fetched_at` は API コンテナの `datetime.now()`（UTC）で
    書かれた naive 値だが、DB セッションの TimeZone は Asia/Tokyo なので
    `now() - fetched_at` は9時間ずれる。ここでは
    「naive UTC の現在時刻」だけを SQL から受け取り、発走時刻の換算と判定は
    Python の純関数側で行う。
    """
    row = (
        await db.execute(
            sql_text("""
                SELECT
                    r.date,
                    r.post_time,
                    (SELECT max(oh.fetched_at)
                       FROM chihou.odds_history oh
                      WHERE oh.race_id = r.id) AS last_fetched_at,
                    (SELECT max(oh.announced_at)
                       FROM chihou.odds_history oh
                      WHERE oh.race_id = r.id) AS last_announced_at,
                    (now() AT TIME ZONE 'UTC') AS now_utc
                FROM chihou.races r
                WHERE r.id = :rid
            """),
            {"rid": race_id},
        )
    ).first()
    if row is None:
        return OddsFreshness(STATUS_MISSING, None, None)

    date, post_time, last_fetched_at, last_announced_at, now_utc = row
    return classify_odds_freshness(
        last_fetched_at=last_fetched_at,
        last_announced_at=last_announced_at,
        now_utc=now_utc,
        post_at_utc=post_time_to_utc(date, post_time),
    )


@router.get("/{race_id}/results")
async def get_chihou_race_results(race_id: int, db: DbDep) -> list[ChihouResultOut]:
    """レースの成績一覧を返す。"""
    result = await db.execute(
        select(
            ChihouRaceResult,
            ChihouHorse.name.label("horse_name"),
        )
        .join(ChihouHorse, ChihouRaceResult.horse_id == ChihouHorse.id)
        .where(ChihouRaceResult.race_id == race_id)
        .order_by(ChihouRaceResult.finish_position.asc().nulls_last())
    )
    rows = result.all()

    return [
        ChihouResultOut(
            horse_number=row[0].horse_number,
            finish_position=row[0].finish_position,
            finish_time=float(row[0].finish_time) if row[0].finish_time is not None else None,
            last_3f=float(row[0].last_3f) if row[0].last_3f is not None else None,
            horse_name=row[1],
        )
        for row in rows
    ]


@router.get("/{race_id}")
async def get_chihou_race(race_id: int, db: DbDep) -> ChihouRaceOut:
    """レース詳細を返す。"""
    result = await db.execute(select(ChihouRace).where(ChihouRace.id == race_id))
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    # has_indices + result_confirmed チェック（並列）
    idx_check, confirmed_check = await asyncio.gather(
        db.execute(
            select(
                exists().where(
                    ChihouCalculatedIndex.race_id == race_id,
                    ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION,
                )
            )
        ),
        db.execute(
            select(
                exists().where(
                    ChihouRaceResult.race_id == race_id,
                    ChihouRaceResult.finish_position.isnot(None),
                )
            )
        ),
    )
    has_indices: bool = idx_check.scalar() or False
    result_confirmed: bool = confirmed_check.scalar() or False

    return ChihouRaceOut(
        id=race.id,
        date=race.date,
        course_name=race.course_name,
        race_number=race.race_number,
        race_name=race.race_name,
        surface=race.surface,
        distance=race.distance,
        grade=race.grade,
        condition=race.condition,
        weather=race.weather,
        head_count=race.head_count,
        post_time=race.post_time,
        has_indices=has_indices,
        result_confirmed=result_confirmed,
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


async def _fetch_chihou_results_payload(race_id: int, db: AsyncSession) -> list[dict]:
    """指定レースの地方競馬成績をWebSocket送信用リストで返す。"""
    stmt = (
        select(ChihouRaceResult, ChihouHorse.name.label("horse_name"))
        .join(ChihouHorse, ChihouRaceResult.horse_id == ChihouHorse.id)
        .where(ChihouRaceResult.race_id == race_id)
        .order_by(ChihouRaceResult.finish_position.asc().nulls_last())
    )
    result = await db.execute(stmt)
    rows = result.all()
    return [
        {
            "horse_number": r.horse_number,
            "finish_position": r.finish_position,
            "finish_time": float(r.finish_time) if r.finish_time is not None else None,
            "last_3f": float(r.last_3f) if r.last_3f is not None else None,
            "horse_name": horse_name,
        }
        for r, horse_name in rows
        if r.finish_position is not None
    ]


def _check_ws_origin(ws: WebSocket) -> None:
    """開発環境以外では Origin ヘッダーを検証する（CSRF対策）。"""
    import os

    origin = ws.headers.get("origin", "")
    allowed = os.environ.get("ALLOWED_ORIGINS", "")
    if not allowed:
        return  # 未設定時はスキップ（開発環境）
    if origin and origin not in allowed.split(","):
        import logging

        logging.getLogger(__name__).warning("WS blocked origin: %r", origin)


@router.websocket("/{race_id}/results/ws")
async def chihou_results_websocket(race_id: int, ws: WebSocket, db: DbDep) -> None:
    """地方競馬成績リアルタイム更新用WebSocket。

    接続時に現在の成績を即送信し、その後は成績確定時にブロードキャストされる
    [{horse_number, finish_position, finish_time, last_3f, horse_name}, ...] を受信する。
    """
    _check_ws_origin(ws)
    await chihou_results_manager.connect(race_id, ws)
    try:
        current = await _fetch_chihou_results_payload(race_id, db)
        if current:
            await ws.send_json(current)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        chihou_results_manager.disconnect(race_id, ws)
