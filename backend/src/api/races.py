"""レース参照APIルーター

DBに格納済みのレース・出馬表・成績データを返すエンドポイント。
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date as _date
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from pydantic import BaseModel
from sqlalchemy import func, select, tuple_
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CalculatedIndex, Horse, Jockey, OddsHistory, Race, RaceEntry, RaceResult, Trainer
from ..db.session import get_db
from ..indices.composite import COMPOSITE_VERSION
from ..indices.confidence import calculate_race_confidence, calculate_recommend_rank
from .ws_manager import manager as ws_manager
from .ws_manager import results_manager

router = APIRouter(prefix="/api/races", tags=["races"])

_ALLOWED_ORIGINS: set[str] = {
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
}


def _check_ws_origin(ws: WebSocket) -> None:
    """WebSocket接続のOriginヘッダーを検証する。

    本番環境で ALLOWED_ORIGINS が設定されている場合のみ検証。
    未設定（空文字）の場合は検証をスキップ（ローカル開発用）。
    """
    if not _ALLOWED_ORIGINS:
        return
    origin = ws.headers.get("origin", "")
    if origin not in _ALLOWED_ORIGINS:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

DbDep = Annotated[AsyncSession, Depends(get_db)]

# JRA 2桁コード → sekito.anagusa course_code
_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK",
    "02": "JHKD",
    "03": "JFKS",
    "04": "JNGT",
    "05": "JTOK",
    "06": "JNKY",
    "07": "JCKO",
    "08": "JKYO",
    "09": "JHSN",
    "10": "JKKR",
}


def _compute_upside_scores(horses: list[HorseIndexOut]) -> None:
    """穴馬スコアをレース内全馬に付与する（in-place）。

    考え方:
      総合指数は低くても、特定の個別指数（コース適性・末脚・穴ぐさ等）が
      レース内で突出している馬に高いスコアを与える。

    スコア = Σ max(0, composite_rank - individual_rank) × weight を正規化

    重みの根拠（2024-2026実績 upside_detection.py 分析）:
      穴ぐさ:     lift +6.3%  → 0.30
      コース適性: lift +5.9%  → 0.28
      パドック:   lift +5.0%  → 0.23
      血統:       lift +3.4%  → 0.10
      騎手:       lift +2.9%  → 0.09
      後3F:       lift +1.7%  → 0.00 (コース適性に包含)
      ローテ:     lift -6.2%  → 除外
      調教:       lift -4.5%  → 除外
      展開:       lift -1.9%  → 除外
      枠順:       lift -2.8%  → 除外
    """
    # 穴馬スコアに使う個別指数と重み（実測リフト比例）
    UPSIDE_WEIGHTS: dict[str, float] = {
        "anagusa_index": 0.30,
        "course_aptitude": 0.28,
        "paddock_index": 0.23,
        "pedigree_index": 0.10,
        "jockey_index": 0.09,
    }

    n = len(horses)
    if n < 2:
        return

    # 総合指数の降順ランク（1=最高）
    sorted_by_composite = sorted(
        range(n), key=lambda i: horses[i].composite_index or 0.0, reverse=True
    )
    composite_ranks = [0] * n
    for rank, idx in enumerate(sorted_by_composite, start=1):
        composite_ranks[idx] = rank

    # 個別指数ランクと突出スコア算出
    raw_scores = [0.0] * n
    for attr, weight in UPSIDE_WEIGHTS.items():
        values = [getattr(h, attr) for h in horses]
        if all(v is None for v in values):
            continue
        # None を最低値で補完してランク付け
        filled = [v if v is not None else 0.0 for v in values]
        sorted_by_attr = sorted(range(n), key=lambda i: filled[i], reverse=True)
        attr_ranks = [0] * n
        for rank, idx in enumerate(sorted_by_attr, start=1):
            attr_ranks[idx] = rank

        # 個別指数ランクが総合ランクよりも上位（数値が小さい）ほど突出
        prominences = [max(0.0, composite_ranks[i] - attr_ranks[i]) for i in range(n)]
        max_prom = max(prominences) if max(prominences) > 0 else 1.0
        for i in range(n):
            raw_scores[i] += (prominences[i] / max_prom) * weight

    # 0〜1 に正規化
    max_score = max(raw_scores) if max(raw_scores) > 0 else 1.0
    for i, h in enumerate(horses):
        h.upside_score = round(raw_scores[i] / max_score, 4)


async def _fetch_anagusa_picks(db: AsyncSession, race: Race) -> dict[int, str]:
    """sekito.anagusa からレースのピック情報を取得する。

    Returns:
        {horse_no: rank} — A/B/C のいずれか
    """
    sekito_code = _JRA_TO_SEKITO.get(race.course)
    if not sekito_code:
        return {}
    race_date = _date(int(race.date[:4]), int(race.date[4:6]), int(race.date[6:8]))
    result = await db.execute(
        _text(
            "SELECT horse_no, rank FROM sekito.anagusa WHERE date = :d AND course_code = :c AND race_no = :r"
        ),
        {"d": race_date, "c": sekito_code, "r": race.race_number},
    )
    rows = result.fetchall()
    return {r[0]: r[1] for r in rows if r[1] in ("A", "B", "C")}


async def _anagusa_picks_for_date(db: AsyncSession, date: str) -> set[tuple[str, int]]:
    """指定日の sekito.anagusa ピック有無を (sekito_code, race_no) セットで返す。"""
    race_date = _date(int(date[:4]), int(date[4:6]), int(date[6:8]))
    result = await db.execute(
        _text("SELECT course_code, race_no FROM sekito.anagusa WHERE date = :d"),
        {"d": race_date},
    )
    rows = result.fetchall()
    return {(r[0], r[1]) for r in rows}


# -------------------------------------------------------------------
# レスポンスモデル
# -------------------------------------------------------------------
class RaceOut(BaseModel):
    """レース情報レスポンス。"""

    id: int
    date: str
    course_name: str
    race_number: int
    race_name: str | None
    surface: str
    distance: int
    grade: str | None
    condition: str | None
    weather: str | None
    head_count: int | None
    jravan_race_id: str | None
    post_time: str | None = None  # 発走時刻 (hhmm形式, 例: "1025")
    race_class_label: str | None = None  # 条件戦クラスラベル（例: "3歳未勝利", "4歳以上2勝クラス"）
    has_indices: bool = False
    has_anagusa: bool = False  # 穴ぐさ指数58以上の馬が存在するか
    confidence_score: int | None = None
    confidence_label: str | None = None  # "HIGH" | "MID" | "LOW"
    confidence_rank: str | None = None   # S / A / B / C
    recommend_rank: str | None = None    # S / A / B / C

    model_config = {"from_attributes": True}


class EntryOut(BaseModel):
    """出馬表エントリーレスポンス。"""

    id: int
    frame_number: int
    horse_number: int
    horse_name: str
    jockey_name: str | None
    trainer_name: str | None
    weight_carried: float | None
    horse_weight: int | None
    weight_change: int | None

    model_config = {"from_attributes": True}


class ResultOut(BaseModel):
    """成績レスポンス。"""

    horse_number: int | None
    finish_position: int | None
    finish_time: float | None
    last_3f: float | None
    horse_name: str

    model_config = {"from_attributes": True}


class RaceConfidence(BaseModel):
    """レース信頼度スコア。"""

    score: int
    label: str           # "HIGH" | "MID" | "LOW"
    rank: str = "C"      # S / A / B / C
    recommend_rank: str = "C"  # S / A / B / C
    gap_1_2: float       # 1位-2位の指数差
    gap_1_3: float       # 1位-3位の指数差
    head_count: int
    win_prob_top: float | None = None
    top_win_odds: float | None = None


class IndicesResponse(BaseModel):
    """指数APIレスポンス（馬リスト + レース信頼度）。"""

    horses: list[HorseIndexOut]
    confidence: RaceConfidence


class HorseIndexOut(BaseModel):
    """1頭分の指数レスポンス。"""

    horse_id: int
    horse_number: int
    horse_name: str
    composite_index: float
    win_probability: float | None  # 勝率予測
    place_probability: float | None  # 複勝率予測（3着以内）
    # 単体指数
    speed_index: float | None
    last3f_index: float | None
    course_aptitude: float | None
    position_advantage: float | None
    jockey_index: float | None
    pace_index: float | None
    rotation_index: float | None
    pedigree_index: float | None
    training_index: float | None
    anagusa_index: float | None
    paddock_index: float | None
    anagusa_rank: str | None = None  # "A" / "B" / "C" / None（ピックなし）
    upside_score: float | None = None  # 穴馬スコア（指数下位でも馬券になりやすい度合い）


class OddsOut(BaseModel):
    """単勝・複勝オッズレスポンス。"""

    win: dict[str, float]  # horse_number (str) → オッズ倍率
    place: dict[str, float]  # horse_number (str) → オッズ倍率（中間値）


# -------------------------------------------------------------------
# エンドポイント
# -------------------------------------------------------------------
@router.get("/nearest-date")
async def get_nearest_race_date(
    db: DbDep,
    from_date: str = Query(..., alias="from", description="基準日 YYYYMMDD"),
    direction: str = Query(..., description="prev | next"),
) -> dict:
    """基準日から最も近い開催日を返す。

    開催データが存在する日付のみ対象とする（平日等はスキップ）。
    """
    if direction == "prev":
        stmt = (
            select(Race.date)
            .where(Race.date < from_date)
            .order_by(Race.date.desc())
            .limit(1)
        )
    else:
        stmt = (
            select(Race.date)
            .where(Race.date > from_date)
            .order_by(Race.date.asc())
            .limit(1)
        )
    result = await db.execute(stmt)
    race_date = result.scalar()
    if not race_date:
        raise HTTPException(status_code=404, detail="No adjacent race date found")
    return {"date": race_date}


@router.get("")
async def list_races(
    db: DbDep,
    date: str = Query(..., description="対象日付 YYYYMMDD"),
    course: str | None = Query(None, description="場コード (01-10) または場名"),
) -> list[RaceOut]:
    """指定日のレース一覧を返す。has_indices=true は指数算出済みを示す。"""
    stmt = select(Race).where(Race.date == date)
    if course:
        if len(course) <= 2 and course.isdigit():
            stmt = stmt.where(Race.course == course)
        else:
            stmt = stmt.where(Race.course_name == course)
    stmt = stmt.order_by(Race.race_number)
    result = await db.execute(stmt)
    races = result.scalars().all()

    # 算出済み指数を一括取得（N+1回避）: 各レースの全 composite_index を取得
    race_ids = [r.id for r in races]
    # 各レースで利用可能な最大バージョンを取得
    best_versions: dict[int, int] = {}
    if race_ids:
        ver_stmt = (
            select(CalculatedIndex.race_id, func.max(CalculatedIndex.version))
            .where(CalculatedIndex.race_id.in_(race_ids))
            .group_by(CalculatedIndex.race_id)
        )
        ver_result = await db.execute(ver_stmt)
        for rid, ver in ver_result.all():
            best_versions[rid] = min(ver, COMPOSITE_VERSION)

    # 各レースの composite_index + win_probability 一覧を取得
    race_indices: dict[int, list[float]] = defaultdict(list)
    race_win_probs: dict[int, list[float]] = defaultdict(list)
    race_top_horse_num: dict[int, int | None] = {}
    if best_versions:
        version_pairs = list({(rid, ver) for rid, ver in best_versions.items()})
        idx_stmt = (
            select(
                CalculatedIndex.race_id,
                CalculatedIndex.composite_index,
                CalculatedIndex.win_probability,
                RaceEntry.horse_number,
            )
            .outerjoin(RaceEntry, (RaceEntry.race_id == CalculatedIndex.race_id) & (RaceEntry.horse_id == CalculatedIndex.horse_id))
            .where(tuple_(CalculatedIndex.race_id, CalculatedIndex.version).in_(version_pairs))
        )
        idx_result = await db.execute(idx_stmt)
        idx_rows_all = idx_result.all()
        for rid, ci, wp, hn in idx_rows_all:
            race_indices[rid].append(float(ci))
            if wp is not None:
                race_win_probs[rid].append(float(wp))
        # トップ馬（最大 composite_index）の horse_number を抽出
        by_race: dict[int, list[tuple]] = defaultdict(list)
        for rid, ci, wp, hn in idx_rows_all:
            by_race[rid].append((float(ci), hn))
        for rid, entries in by_race.items():
            best = max(entries, key=lambda x: x[0])
            race_top_horse_num[rid] = best[1]

    indexed_ids = {rid for rid in best_versions if race_indices.get(rid)}

    # トップ馬の最新単勝オッズをバッチ取得
    top_horse_win_odds: dict[int, float] = {}
    if indexed_ids:
        odds_rows = await db.execute(
            select(
                OddsHistory.race_id,
                OddsHistory.combination,
                OddsHistory.odds,
                OddsHistory.fetched_at,
            )
            .where(OddsHistory.race_id.in_(list(indexed_ids)))
            .where(OddsHistory.bet_type == "win")
            .order_by(OddsHistory.race_id, OddsHistory.fetched_at.desc())
        )
        seen_races_odds: set[int] = set()
        for rid, combo, odds, _ in odds_rows.all():
            top_hn = race_top_horse_num.get(rid)
            if top_hn is not None and combo == str(top_hn) and rid not in seen_races_odds:
                if odds is not None:
                    top_horse_win_odds[rid] = float(odds)
                seen_races_odds.add(rid)

    # has_anagusa: sekito.anagusa のピック有無で判定（スコア閾値でなく実ピック）
    anagusa_picks_set = await _anagusa_picks_for_date(db, date)

    result_list = []
    for r in races:
        out = RaceOut.model_validate(r)
        out.has_indices = r.id in indexed_ids
        sekito_code = _JRA_TO_SEKITO.get(r.course)
        out.has_anagusa = bool(sekito_code and (sekito_code, r.race_number) in anagusa_picks_set)
        if r.id in indexed_ids:
            wp_list = race_win_probs.get(r.id) or None
            conf = calculate_race_confidence(race_indices[r.id], r.head_count, wp_list)
            out.confidence_score = conf["score"]
            out.confidence_label = conf["label"]
            out.confidence_rank = conf["rank"]
            out.recommend_rank = calculate_recommend_rank(
                conf["score"],
                conf.get("win_prob_top"),
                top_horse_win_odds.get(r.id),
            )
        result_list.append(out)
    return result_list


@router.get("/{race_id}")
async def get_race(race_id: int, db: DbDep) -> RaceOut:
    """レース詳細を返す。"""
    result = await db.execute(select(Race).where(Race.id == race_id))
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    return RaceOut.model_validate(race)


@router.get("/{race_id}/entries")
async def get_entries(race_id: int, db: DbDep) -> list[EntryOut]:
    """レースの出馬表を返す。"""
    race_result = await db.execute(select(Race).where(Race.id == race_id))
    race = race_result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    stmt = (
        select(RaceEntry, Horse, Jockey, Trainer)
        .join(Horse, RaceEntry.horse_id == Horse.id)
        .outerjoin(Jockey, RaceEntry.jockey_id == Jockey.id)
        .outerjoin(Trainer, RaceEntry.trainer_id == Trainer.id)
        .where(RaceEntry.race_id == race_id)
        .order_by(RaceEntry.horse_number)
    )
    entries_result = await db.execute(stmt)
    entries = entries_result.all()

    result = []
    for entry, horse, jockey, trainer in entries:
        result.append(
            EntryOut(
                id=entry.id,
                frame_number=entry.frame_number,
                horse_number=entry.horse_number,
                horse_name=horse.name,
                jockey_name=jockey.name if jockey else None,
                trainer_name=trainer.name if trainer else None,
                weight_carried=float(entry.weight_carried) if entry.weight_carried else None,
                horse_weight=entry.horse_weight,
                weight_change=entry.weight_change,
            )
        )
    return result


@router.get("/{race_id}/indices")
async def get_indices(race_id: int, db: DbDep) -> IndicesResponse:
    """レースの算出指数一覧を返す（composite_index 降順）。

    win_probability / place_probability は Softmax + Harville 式で算出。
    未算出の場合は null を返す。
    """
    race_result = await db.execute(select(Race).where(Race.id == race_id))
    race = race_result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    # v7 がなければ最新バージョンにフォールバック
    ver_result = await db.execute(
        select(CalculatedIndex.version)
        .where(CalculatedIndex.race_id == race_id)
        .order_by(CalculatedIndex.version.desc())
        .limit(1)
    )
    latest_version = ver_result.scalar()
    if latest_version is None:
        raise HTTPException(status_code=404, detail="No indices calculated for this race")
    use_version = COMPOSITE_VERSION if latest_version >= COMPOSITE_VERSION else latest_version

    stmt = (
        select(CalculatedIndex, RaceEntry, Horse)
        .join(
            RaceEntry,
            (RaceEntry.race_id == CalculatedIndex.race_id)
            & (RaceEntry.horse_id == CalculatedIndex.horse_id),
        )
        .join(Horse, Horse.id == CalculatedIndex.horse_id)
        .where(CalculatedIndex.race_id == race_id)
        .where(CalculatedIndex.version == use_version)
        .order_by(CalculatedIndex.composite_index.desc().nullslast())
    )
    rows_result = await db.execute(stmt)
    rows = rows_result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No indices calculated for this race")

    # race_entries に (race_id, horse_id) の重複がある場合に備え、horse_id で重複排除
    seen: set[int] = set()
    unique_rows = []
    for row in rows:
        if row[0].horse_id not in seen:
            seen.add(row[0].horse_id)
            unique_rows.append(row)

    def _f(v) -> float | None:
        return float(v) if v is not None else None

    horses = [
        HorseIndexOut(
            horse_id=horse.id,
            horse_number=entry.horse_number,
            horse_name=horse.name,
            composite_index=float(ci.composite_index),
            win_probability=_f(ci.win_probability),
            place_probability=_f(ci.place_probability),
            speed_index=_f(ci.speed_index),
            last3f_index=_f(ci.last_3f_index),
            course_aptitude=_f(ci.course_aptitude),
            position_advantage=_f(ci.position_advantage),
            jockey_index=_f(ci.jockey_index),
            pace_index=_f(ci.pace_index),
            rotation_index=_f(ci.rotation_index),
            pedigree_index=_f(ci.pedigree_index),
            training_index=_f(ci.training_index),
            anagusa_index=_f(ci.anagusa_index),
            paddock_index=_f(ci.paddock_index),
        )
        for ci, entry, horse in unique_rows
    ]

    # sekito.anagusa からランク情報を付与
    picks = await _fetch_anagusa_picks(db, race)
    for h in horses:
        h.anagusa_rank = picks.get(h.horse_number)

    # 穴馬スコア算出（指数下位でも特定個別指数が突出している度合い）
    _compute_upside_scores(horses)

    wp_list = [h.win_probability for h in horses if h.win_probability is not None]
    conf_data = calculate_race_confidence(
        composite_indices=[h.composite_index for h in horses],
        head_count=race.head_count,
        win_probabilities=wp_list or None,
    )

    # トップ馬の最新単勝オッズ取得
    top_win_odds: float | None = None
    if horses and horses[0].horse_number is not None:
        odds_row = await db.execute(
            select(OddsHistory.odds)
            .where(OddsHistory.race_id == race_id)
            .where(OddsHistory.bet_type == "win")
            .where(OddsHistory.combination == str(horses[0].horse_number))
            .order_by(OddsHistory.fetched_at.desc())
            .limit(1)
        )
        odds_val = odds_row.scalar()
        if odds_val is not None:
            top_win_odds = float(odds_val)

    rec_rank = calculate_recommend_rank(conf_data["score"], conf_data.get("win_prob_top"), top_win_odds)

    return IndicesResponse(
        horses=horses,
        confidence=RaceConfidence(
            **conf_data,
            recommend_rank=rec_rank,
            top_win_odds=top_win_odds,
        ),
    )


@router.get("/{race_id}/results")
async def get_results(race_id: int, db: DbDep) -> list[ResultOut]:
    """レースの成績を返す。"""
    race_result = await db.execute(select(Race).where(Race.id == race_id))
    race = race_result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    stmt = (
        select(RaceResult, Horse)
        .join(Horse, RaceResult.horse_id == Horse.id)
        .where(RaceResult.race_id == race_id)
        .order_by(RaceResult.finish_position.asc().nullslast())
    )
    results_result = await db.execute(stmt)
    results = results_result.all()

    return [
        ResultOut(
            horse_number=r.horse_number,
            finish_position=r.finish_position,
            finish_time=float(r.finish_time) if r.finish_time else None,
            last_3f=float(r.last_3f) if r.last_3f else None,
            horse_name=h.name,
        )
        for r, h in results
    ]


@router.get("/{race_id}/odds")
async def get_odds(race_id: int, db: DbDep) -> OddsOut:
    """レースの最新単勝・複勝オッズを馬番ごとに返す。"""
    win_odds: dict[str, float] = {}
    place_odds: dict[str, float] = {}

    for bet_type, target in (("win", win_odds), ("place", place_odds)):
        # MAX(fetched_at) をサブクエリ化し、1クエリで最新オッズを取得
        latest_at_subq = (
            select(func.max(OddsHistory.fetched_at))
            .where(OddsHistory.race_id == race_id, OddsHistory.bet_type == bet_type)
            .scalar_subquery()
        )
        stmt = select(OddsHistory).where(
            OddsHistory.race_id == race_id,
            OddsHistory.bet_type == bet_type,
            OddsHistory.fetched_at == latest_at_subq,
        )
        odds_result = await db.execute(stmt)
        rows = odds_result.scalars().all()
        for row in rows:
            if row.odds is not None:
                target[row.combination] = float(row.odds)

    return OddsOut(win=win_odds, place=place_odds)


@router.websocket("/{race_id}/odds/ws")
async def odds_websocket(race_id: int, ws: WebSocket) -> None:
    """オッズリアルタイム更新用WebSocket。

    接続後、オッズが更新されるたびに {"win": {...}, "place": {...}} を送信する。
    """
    _check_ws_origin(ws)
    await ws_manager.connect(race_id, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(race_id, ws)


@router.websocket("/{race_id}/results/ws")
async def results_websocket(race_id: int, ws: WebSocket, db: DbDep) -> None:
    """成績リアルタイム更新用WebSocket。

    接続時に現在の成績を即送信し、その後は成績確定時にブロードキャストされる
    [{horse_number, finish_position, finish_time, last_3f, horse_name}, ...] を受信する。
    """
    _check_ws_origin(ws)
    await results_manager.connect(race_id, ws)
    try:
        # 接続時に現在の成績を即送信（ページリロード不要にするため）
        current = await _fetch_results_payload(race_id, db)
        if current:
            await ws.send_json(current)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        results_manager.disconnect(race_id, ws)


async def _fetch_results_payload(race_id: int, db: AsyncSession) -> list[dict]:
    """指定レースの成績をWebSocket送信用リストで返す。"""
    from ..db.models import Horse, RaceResult

    stmt = (
        select(RaceResult, Horse)
        .join(Horse, RaceResult.horse_id == Horse.id)
        .where(RaceResult.race_id == race_id)
        .order_by(RaceResult.finish_position.asc().nullslast())
    )
    result = await db.execute(stmt)
    rows = result.all()
    return [
        {
            "horse_number": r.horse_number,
            "finish_position": r.finish_position,
            "finish_time": float(r.finish_time) if r.finish_time else None,
            "last_3f": float(r.last_3f) if r.last_3f else None,
            "horse_name": h.name,
        }
        for r, h in rows
        if r.finish_position is not None
    ]
