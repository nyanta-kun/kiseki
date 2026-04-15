"""地方競馬 レース参照APIルーター"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouHorse,
    ChihouOddsHistory,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRaceResult,
)
from ..db.session import get_db
from ..indices.buy_signal import chihou_buy_signal
from ..indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION
from ..indices.confidence import calculate_race_confidence, calculate_recommend_rank

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
    confidence_rank: str | None = None   # S / A / B / C
    recommend_rank: str | None = None    # S / A / B / C
    buy_signal: str | None = None        # "buy" | "caution" | "pass"
    top_win_odds: float | None = None    # 指数1位馬の単勝オッズ

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


class ChihouRaceRanks(BaseModel):
    """地方競馬 レース信頼度・推奨度ランク"""

    score: int
    confidence_rank: str  # S / A / B / C
    recommend_rank: str   # S / A / B / C
    gap_1_2: float
    gap_1_3: float
    win_prob_top: float | None = None
    top_win_odds: float | None = None


class ChihouIndicesResponse(BaseModel):
    """地方競馬 指数レスポンス"""

    horses: list[ChihouHorseIndexOut]
    ranks: ChihouRaceRanks | None = None


class ChihouResultOut(BaseModel):
    """地方競馬 成績レスポンス（JRA RaceResult 型互換）"""

    horse_number: int | None = None
    finish_position: int | None = None
    finish_time: float | None = None
    last_3f: float | None = None
    horse_name: str


@router.get("/race-keys")
async def get_chihou_race_keys(
    date: str = Query(..., description="開催日 YYYYMMDD"),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """指定日の地方競馬レースキー（umaconn_race_id）一覧を返す。UmaConnエージェント用。"""
    result = await db.execute(
        select(ChihouRace.id, ChihouRace.umaconn_race_id)
        .where(ChihouRace.date == date)
        .where(ChihouRace.umaconn_race_id.isnot(None))
        .order_by(ChihouRace.race_number)
    )
    rows = result.all()
    return [{"id": row[0], "race_key": row[1]} for row in rows]


@router.get("")
async def get_chihou_races_by_date(
    date: str = Query(..., description="開催日 YYYYMMDD"),
    db: AsyncSession = Depends(get_db),
) -> list[ChihouRaceOut]:
    """指定日の地方競馬レース一覧を返す。"""
    result = await db.execute(
        select(ChihouRace)
        .where(ChihouRace.date == date)
        .order_by(ChihouRace.race_number)
    )
    races = result.scalars().all()

    if not races:
        return []

    race_ids = [r.id for r in races]

    # --- 指数バッチ取得（信頼度算出用） ---
    idx_rows = await db.execute(
        select(
            ChihouCalculatedIndex.race_id,
            ChihouCalculatedIndex.composite_index,
            ChihouCalculatedIndex.win_probability,
            ChihouRaceEntry.horse_number,
        )
        .outerjoin(
            ChihouRaceEntry,
            (ChihouRaceEntry.race_id == ChihouCalculatedIndex.race_id)
            & (ChihouRaceEntry.horse_id == ChihouCalculatedIndex.horse_id),
        )
        .where(ChihouCalculatedIndex.race_id.in_(race_ids))
        .where(ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION)
    )
    # race_id → [(composite_index, win_probability, horse_number)]
    race_index_rows: dict[int, list[tuple]] = defaultdict(list)
    for rid, ci, wp, hn in idx_rows.all():
        race_index_rows[rid].append((float(ci) if ci is not None else 0.0, wp, hn))

    indexed_race_ids = set(race_index_rows.keys())

    # 各レースのトップ馬（composite_index 最大）の horse_number を特定
    top_horse_numbers: dict[int, int | None] = {}
    for rid, entries in race_index_rows.items():
        best = max(entries, key=lambda x: x[0])
        top_horse_numbers[rid] = best[2]  # horse_number

    # --- 最新単勝オッズ取得（トップ馬対象） ---
    # odds_history から各レースの最新 win オッズを取得し Python 側でトップ馬を絞り込む
    latest_win_odds: dict[int, float] = {}
    if indexed_race_ids:
        odds_rows = await db.execute(
            select(
                ChihouOddsHistory.race_id,
                ChihouOddsHistory.combination,
                ChihouOddsHistory.odds,
                ChihouOddsHistory.fetched_at,
            )
            .where(ChihouOddsHistory.race_id.in_(list(indexed_race_ids)))
            .where(ChihouOddsHistory.bet_type == "win")
            .order_by(ChihouOddsHistory.race_id, ChihouOddsHistory.fetched_at.desc())
        )
        # 各 race_id のトップ馬のみ最新オッズを保持
        seen_races: set[int] = set()
        for rid, combo, odds, _ in odds_rows.all():
            top_hn = top_horse_numbers.get(rid)
            if top_hn is not None and combo == str(top_hn) and rid not in seen_races:
                if odds is not None:
                    latest_win_odds[rid] = float(odds)
                seen_races.add(rid)

    # --- 信頼度・推奨度算出 ---
    confidence_data: dict[int, dict] = {}
    for rid, entries in race_index_rows.items():
        ci_list = [e[0] for e in entries]
        wp_list = [float(e[1]) for e in entries if e[1] is not None]
        race_obj = next((r for r in races if r.id == rid), None)
        conf = calculate_race_confidence(
            ci_list,
            race_obj.head_count if race_obj else None,
            wp_list or None,
        )
        top_wp = conf.get("win_prob_top")
        win_odds = latest_win_odds.get(rid)
        conf["recommend_rank"] = calculate_recommend_rank(
            conf["score"], top_wp, win_odds
        )
        confidence_data[rid] = conf

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
            confidence_score=confidence_data[race.id]["score"] if race.id in confidence_data else None,
            confidence_label=confidence_data[race.id]["label"] if race.id in confidence_data else None,
            confidence_rank=confidence_data[race.id]["rank"] if race.id in confidence_data else None,
            recommend_rank=confidence_data[race.id]["recommend_rank"] if race.id in confidence_data else None,
            buy_signal=chihou_buy_signal(race.course_name),
            top_win_odds=latest_win_odds.get(race.id),
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
            .order_by(ChihouRace.date.desc())
            .limit(1)
        )
    else:
        result = await db.execute(
            select(ChihouRace.date)
            .where(ChihouRace.date > from_)
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
        JOIN sekito.racecourse rc ON r.course = rc.netkeiba_id
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
            r[0]: (float(r[1]) if r[1] is not None else None,
                   float(r[2]) if r[2] is not None else None)
            for r in ext_rows
        }
        kichi_entries = [(hn, v[0]) for hn, v in ext_dict.items() if v[0] is not None]
        netk_entries = [(hn, v[1]) for hn, v in ext_dict.items() if v[1] is not None]
        kichi_top = max(kichi_entries, key=lambda x: x[1])[0] if kichi_entries else None
        netk_top = max(netk_entries, key=lambda x: x[1])[0] if netk_entries else None

        if kichi_top is not None or netk_top is not None:
            for hn in ext_dict:
                consensus_map[hn] = (1 if hn == kichi_top else 0) + (1 if hn == netk_top else 0)

    horses = []
    for row in rows:
        ci: ChihouCalculatedIndex = row[0]
        horse_name: str = row[1]
        horse_number: int | None = row[2]
        horses.append(
            ChihouHorseIndexOut(
                horse_id=ci.horse_id,
                horse_number=horse_number,
                horse_name=horse_name,
                composite_index=float(ci.composite_index) if ci.composite_index is not None else 0.0,
                win_probability=float(ci.win_probability) if ci.win_probability is not None else None,
                place_probability=float(ci.place_probability) if ci.place_probability is not None else None,
                speed_index=float(ci.speed_index) if ci.speed_index is not None else None,
                last3f_index=float(ci.last3f_index) if ci.last3f_index is not None else None,
                jockey_index=float(ci.jockey_index) if ci.jockey_index is not None else None,
                rotation_index=float(ci.rotation_index) if ci.rotation_index is not None else None,
                last_margin_index=float(ci.last_margin_index) if ci.last_margin_index is not None else None,
                place_ev_index=float(ci.place_ev_index) if ci.place_ev_index is not None else None,
                external_consensus=consensus_map.get(horse_number) if (consensus_map and horse_number is not None) else None,
            )
        )

    # --- 信頼度・推奨度ランク算出 ---
    ranks: ChihouRaceRanks | None = None
    if horses:
        ci_list = [h.composite_index for h in horses]
        wp_list = [h.win_probability for h in horses if h.win_probability is not None]

        # レース情報（head_count）取得
        race_row = await db.execute(select(ChihouRace).where(ChihouRace.id == race_id))
        race_obj = race_row.scalar_one_or_none()

        conf = calculate_race_confidence(ci_list, race_obj.head_count if race_obj else None, wp_list or None)

        # トップ馬の単勝オッズを取得
        top_horse = horses[0]  # すでに composite_index 降順ソート済み
        top_win_odds: float | None = None
        if top_horse.horse_number is not None:
            odds_row = await db.execute(
                select(ChihouOddsHistory.odds)
                .where(ChihouOddsHistory.race_id == race_id)
                .where(ChihouOddsHistory.bet_type == "win")
                .where(ChihouOddsHistory.combination == str(top_horse.horse_number))
                .order_by(ChihouOddsHistory.fetched_at.desc())
                .limit(1)
            )
            odds_val = odds_row.scalar()
            if odds_val is not None:
                top_win_odds = float(odds_val)

        ranks = ChihouRaceRanks(
            score=conf["score"],
            confidence_rank=conf["rank"],
            recommend_rank=calculate_recommend_rank(conf["score"], conf.get("win_prob_top"), top_win_odds),
            gap_1_2=conf["gap_1_2"],
            gap_1_3=conf["gap_1_3"],
            win_prob_top=conf.get("win_prob_top"),
            top_win_odds=top_win_odds,
        )

    return ChihouIndicesResponse(horses=horses, ranks=ranks)


@router.get("/{race_id}/odds")
async def get_chihou_race_odds(race_id: int, db: DbDep) -> dict:
    """レースの最新単勝・複勝オッズを返す。

    odds_history から各馬の最新オッズを取得して返す。
    JRA の `/races/{id}/odds` と同一スキーマ（win/place の馬番→倍率 dict）。
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
    return {"win": win, "place": place}


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

    # has_indices チェック
    idx_check = await db.execute(
        select(
            exists().where(
                ChihouCalculatedIndex.race_id == race_id,
                ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION,
            )
        )
    )
    has_indices: bool = idx_check.scalar() or False

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
    )
