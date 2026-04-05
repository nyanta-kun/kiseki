"""AI指数精度実績 API

calculated_indices の composite_index 予測 vs race_results の実際着順を集計し、
指数モデルの予測精度（的中率・回収率シミュレーション）を返す。

指標定義:
  - 単勝的中率: 予測1位馬（composite_index 最高）が実際1着になった割合
  - 複勝的中率: 予測1位馬が実際3着以内に入った割合
  - top3カバー率: 実際3着以内の馬が予測top3に含まれていた割合
  - 単勝シミュレーションROI: 毎レース予測1位に100円賭けた場合の回収率
  - 複勝シミュレーションROI: 毎レース予測1位に100円複勝を購入した場合の回収率（複勝オッズあり分のみ）
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CalculatedIndex, Race, RaceResult
from ..db.session import get_db
from ..indices.confidence import calculate_race_confidence

router = APIRouter(prefix="/api/performance", tags=["performance"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# ディメンション分類ヘルパー
# ---------------------------------------------------------------------------

def _condition_label(
    grade: str | None,
    race_type_code: str | None,
    prize_1st: int | None,
) -> str:
    """レース条件カテゴリラベルを返す。"""
    if grade:
        if grade in ("G1", "G2", "G3"):
            return grade
        return "OP・L"
    tc = race_type_code or ""
    if tc in ("18", "19"):
        return "障害"
    p = prize_1st or 0
    if not p:
        return "条件戦"
    if tc == "11":   # 2歳
        return "未勝利" if p <= 58000 else "1勝"
    if tc == "12":   # 3歳
        return "未勝利" if p <= 62000 else "1勝" if p <= 74000 else "2勝"
    if tc == "13":   # 3歳以上
        return "1勝" if p <= 100000 else "2勝" if p <= 130000 else "3勝"
    if tc == "14":   # 4歳以上
        return "2勝" if p <= 100000 else "3勝"
    return "条件戦"


# key: フロントエンドの URL パラメータキー（ASCII）
# label: 表示・集計ラベル（日本語）
_DISTANCE_KEY_MAP: dict[str, tuple[str, int, int]] = {
    "sprint": ("短距離(〜1400m)",   0,    1400),
    "mile":   ("マイル(1401〜1799m)", 1401, 1799),
    "middle": ("中距離(1800〜2200m)", 1800, 2200),
    "long":   ("長距離(2201m〜)",   2201, 99999),
}
# 後方互換: 旧ラベル → ラベルのまま（直接ラベル指定時）
_DISTANCE_LABEL_TO_KEY: dict[str, str] = {v[0]: k for k, v in _DISTANCE_KEY_MAP.items()}


_SURFACE_NORMALIZE: dict[str, str] = {
    "10": "芝",  # track_code 1x = 芝
    "11": "芝", "12": "芝", "13": "芝", "14": "芝", "15": "芝", "16": "芝", "17": "芝",
    "20": "ダ",  # track_code 2x = ダート
    "21": "ダ", "22": "ダ", "23": "ダ", "24": "ダ", "25": "ダ", "26": "ダ", "27": "ダ",
    "51": "障", "52": "障", "53": "障", "54": "障", "55": "障",  # 5x = 障害
}


def _normalize_surface(surface: str | None) -> str:
    """track_code が残存している surface 値を正規化する。"""
    if not surface:
        return "不明"
    return _SURFACE_NORMALIZE.get(surface, surface)


def _distance_range_label(distance: int | None) -> str:
    if not distance:
        return "不明"
    for _key, (label, lo, hi) in _DISTANCE_KEY_MAP.items():
        if lo <= distance <= hi:
            return label
    return "不明"


# ---------------------------------------------------------------------------
# レスポンスモデル
# ---------------------------------------------------------------------------


class ConfidenceStats(BaseModel):
    """信頼度グループ別の成績集計。"""

    total_races: int
    win_hit_rate: float        # 単勝的中率 0-1
    place_hit_rate: float      # 複勝的中率 0-1
    top3_coverage_rate: float  # top3カバー率 0-1
    simulated_roi_win: float   # 単勝シミュレーション回収率
    simulated_roi_place: float  # 複勝シミュレーション回収率（複勝オッズあり分のみ）
    place_roi_races: int        # 複勝ROI算出対象レース数


class DimensionStat(BaseModel):
    """ディメンション（競馬場・馬場・距離・条件）別の成績集計。"""

    label: str
    total_races: int
    win_hit_rate: float
    place_hit_rate: float
    top3_coverage_rate: float
    simulated_roi_win: float
    simulated_roi_place: float
    place_roi_races: int


class MonthlyStats(BaseModel):
    """月次成績集計。"""

    year_month: str
    total_races: int
    win_hit_rate: float
    place_hit_rate: float
    top3_coverage_rate: float
    simulated_roi_win: float
    simulated_roi_place: float
    place_roi_races: int
    breakdown: dict[str, ConfidenceStats | None]  # HIGH / MID / LOW


class PerformanceSummaryOut(BaseModel):
    """AI指数精度実績サマリーレスポンス。"""

    from_date: str
    to_date: str
    total_races: int
    win_hit_rate: float
    place_hit_rate: float
    top3_coverage_rate: float
    simulated_roi_win: float
    simulated_roi_place: float
    place_roi_races: int
    breakdown: dict[str, ConfidenceStats | None]   # 信頼度別
    monthly_stats: list[MonthlyStats]              # 月次推移
    by_course: list[DimensionStat]                 # 競馬場別
    by_surface: list[DimensionStat]                # 馬場別
    by_distance_range: list[DimensionStat]         # 距離帯別
    by_condition: list[DimensionStat]              # 条件別


# ---------------------------------------------------------------------------
# 集計ヘルパー
# ---------------------------------------------------------------------------


def _agg(items: list[dict]) -> ConfidenceStats | None:
    """レース指標リストから集計値を算出する。items が空の場合は None を返す。"""
    if not items:
        return None
    total = len(items)
    place_items = [i for i in items if i["has_place_odds"]]
    place_total = len(place_items)
    return ConfidenceStats(
        total_races=total,
        win_hit_rate=round(sum(1 for i in items if i["win_hit"]) / total, 4),
        place_hit_rate=round(sum(1 for i in items if i["place_hit"]) / total, 4),
        top3_coverage_rate=round(sum(i["top3_coverage"] for i in items) / total, 4),
        simulated_roi_win=round(
            sum(i["roi_contribution_win"] for i in items) / (total * 100), 4
        ),
        simulated_roi_place=round(
            sum(i["roi_contribution_place"] for i in place_items) / (place_total * 100), 4
        ) if place_total else 0.0,
        place_roi_races=place_total,
    )


def _dim_stats(
    groups: dict[str, list[dict]],
    order: list[str] | None = None,
    sort_by_races: bool = False,
) -> list[DimensionStat]:
    """ディメンション別の DimensionStat リストを生成する。"""
    result = []
    keys: list[str]
    if order:
        keys = [k for k in order if k in groups]
        keys += [k for k in sorted(groups) if k not in order]
    elif sort_by_races:
        keys = sorted(groups, key=lambda k: len(groups[k]), reverse=True)
    else:
        keys = sorted(groups)

    for k in keys:
        agg = _agg(groups[k])
        if not agg:
            continue
        result.append(
            DimensionStat(
                label=k,
                total_races=agg.total_races,
                win_hit_rate=agg.win_hit_rate,
                place_hit_rate=agg.place_hit_rate,
                top3_coverage_rate=agg.top3_coverage_rate,
                simulated_roi_win=agg.simulated_roi_win,
                simulated_roi_place=agg.simulated_roi_place,
                place_roi_races=agg.place_roi_races,
            )
        )
    return result


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------

_CONDITION_ORDER = [
    "G1", "G2", "G3", "OP・L",
    "3勝", "2勝", "1勝", "未勝利",
    "障害", "条件戦",
]
_DISTANCE_ORDER = [v[0] for v in _DISTANCE_KEY_MAP.values()]
_SURFACE_ORDER = ["芝", "ダ", "障"]

# JRA 10場のコードセット（フィルタ用）
_JRA_COURSE_CODES = {"01", "02", "03", "04", "05", "06", "07", "08", "09", "10"}

# 競馬場別表示順: JRA10場 → 地方 → 海外（各グループ内はレース数降順）
_JRA_COURSE_NAMES = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
_LOCAL_COURSE_NAMES = [
    "門別", "盛岡", "水沢", "浦和", "船橋", "大井", "川崎",
    "笠松", "名古屋", "園田", "姫路", "高知", "佐賀",
    "旭川(廃止)", "高崎(廃止)", "荒尾(廃止)",
    "廃止場(56)", "廃止場(57)", "廃止場(58)", "廃止場(59)", "新潟(地方廃止)",
]
_OVERSEAS_COURSE_NAMES = [
    "香港", "UAE", "サウジアラビア", "カタール", "バーレーン",
    "英国", "フランス", "アイルランド", "ドイツ", "イタリア",
    "米国", "カナダ", "オーストラリア", "韓国",
]


def _sort_by_course_group(groups: dict[str, list[dict]]) -> list[DimensionStat]:
    """競馬場別を JRA → 地方 → 海外 の順で、各グループ内はレース数降順で返す。"""
    def make_stats(names: list[str]) -> list[DimensionStat]:
        subset = {k: v for k, v in groups.items() if k in names}
        return _dim_stats(subset, sort_by_races=True)

    # 上記リストに含まれない名前はグループ不明として地方扱い
    known = set(_JRA_COURSE_NAMES) | set(_LOCAL_COURSE_NAMES) | set(_OVERSEAS_COURSE_NAMES)
    unknown = {k: v for k, v in groups.items() if k not in known}

    return (
        make_stats(_JRA_COURSE_NAMES)
        + make_stats(_LOCAL_COURSE_NAMES)
        + _dim_stats(unknown, sort_by_races=True)  # 未知コードは地方扱い
        + make_stats(_OVERSEAS_COURSE_NAMES)
    )


@router.get("/summary", response_model=PerformanceSummaryOut)
async def get_performance_summary(
    db: DbDep,
    from_date: str | None = Query(
        default=None, description="集計開始日 YYYYMMDD（デフォルト: 前年1月1日）"
    ),
    to_date: str | None = Query(
        default=None, description="集計終了日 YYYYMMDD（デフォルト: 今日）"
    ),
    course_name: str | None = Query(default=None, description="競馬場名（カンマ区切り複数可: 東京,中山）"),
    surface: str | None = Query(default=None, description="馬場（カンマ区切り複数可: 芝,ダ）"),
    distance_range: str | None = Query(
        default=None,
        description="距離帯（カンマ区切り複数可: 短距離(〜1400m),マイル(1401〜1799m)）",
    ),
    condition: str | None = Query(
        default=None,
        description="条件（カンマ区切り複数可: G1,G2,G3）",
    ),
) -> PerformanceSummaryOut:
    """AI指数の予測精度サマリーを返す。

    - composite_index 1位予測 vs 実際の着順を集計
    - 信頼度（HIGH/MID/LOW）別内訳・月次推移・ディメンション別集計を含む
    - フィルタ: 期間・競馬場・馬場・距離帯・条件（複数選択可）

    デフォルト集計期間: 前年1月1日〜今日
    """
    # --- デフォルト日付 ---
    today = _date.today()
    if from_date is None:
        from_date = f"{today.year - 1}0101"
    if to_date is None:
        to_date = today.strftime("%Y%m%d")

    # --- カンマ区切りパラメータを展開 ---
    def _split(v: str | None) -> list[str] | None:
        if not v:
            return None
        items = [s.strip() for s in v.split(",") if s.strip()]
        return items if items else None

    course_names = _split(course_name)
    surfaces = _split(surface)
    distance_ranges = _split(distance_range)
    conditions = _split(condition)

    # --- 各 (race_id, horse_id) の最新バージョンサブクエリ ---
    # バージョンごとに集計期間が異なるため、固定バージョンでなく
    # 各レース・各馬ごとの最大バージョンを使用して集計する。
    latest_version_sq = (
        select(
            CalculatedIndex.race_id.label("lv_race_id"),
            CalculatedIndex.horse_id.label("lv_horse_id"),
            func.max(CalculatedIndex.version).label("lv_max_version"),
        )
        .group_by(CalculatedIndex.race_id, CalculatedIndex.horse_id)
        .subquery()
    )

    # --- SQLフィルタ条件の構築 ---
    sql_conditions = [
        Race.date >= from_date,
        Race.date <= to_date,
        RaceResult.finish_position.is_not(None),
        Race.course.in_(list(_JRA_COURSE_CODES)),
    ]
    if course_names:
        sql_conditions.append(Race.course_name.in_(course_names))
    if surfaces:
        sql_conditions.append(Race.surface.in_(surfaces))
    if distance_ranges:
        dist_conds = []
        for dr in distance_ranges:
            # キー（sprint/mile/middle/long）または旧ラベルを両方受け付ける
            entry = _DISTANCE_KEY_MAP.get(dr) or _DISTANCE_KEY_MAP.get(
                _DISTANCE_LABEL_TO_KEY.get(dr, ""), None
            )
            if entry:
                _, lo, hi = entry
                dist_conds.append((Race.distance >= lo) & (Race.distance <= hi))
        if dist_conds:
            sql_conditions.append(or_(*dist_conds))
    # condition フィルタは Python 側で適用（race_class_label が DB カラムでないため）

    # --- 全馬 composite_index + 着順を一括取得 ---
    rows = (
        await db.execute(
            select(
                Race.id.label("race_id"),
                Race.date.label("race_date"),
                Race.head_count.label("head_count"),
                Race.course_name.label("course_name"),
                Race.surface.label("surface"),
                Race.distance.label("distance"),
                Race.grade.label("grade"),
                Race.race_type_code.label("race_type_code"),
                Race.prize_1st.label("prize_1st"),
                CalculatedIndex.horse_id,
                CalculatedIndex.composite_index,
                RaceResult.finish_position,
                RaceResult.horse_number,
                RaceResult.win_odds,
            )
            .join(CalculatedIndex, CalculatedIndex.race_id == Race.id)
            .join(
                latest_version_sq,
                (latest_version_sq.c.lv_race_id == CalculatedIndex.race_id)
                & (latest_version_sq.c.lv_horse_id == CalculatedIndex.horse_id)
                & (latest_version_sq.c.lv_max_version == CalculatedIndex.version),
            )
            .join(
                RaceResult,
                (RaceResult.race_id == Race.id)
                & (RaceResult.horse_id == CalculatedIndex.horse_id),
            )
            .where(*sql_conditions)
            .order_by(Race.date, Race.id)
        )
    ).fetchall()

    # --- レースごとにグループ化 ---
    race_groups: dict[int, list] = defaultdict(list)
    for row in rows:
        race_groups[row.race_id].append(row)

    # --- 複勝オッズを race_payouts → odds_history の順で一括取得 ---
    # race_payouts に確定払戻が存在する場合はそちらを優先し、
    # なければ odds_history の最新オッズにフォールバックする。
    race_ids = list(race_groups.keys())
    place_odds_map: dict[tuple[int, int], float] = {}
    # race_payouts でカバーされているレース（結果確定済み）の race_id セット。
    # race_payouts には 3 着以内の馬の払戻しか存在しないため、
    # 予測 1 位馬が着外の場合は place_odds_map にエントリがない。
    # has_place_odds フラグはこのセットを参照して「賭けた」を正しく判定する。
    payout_covered_race_ids: set[int] = set()
    if race_ids:
        # 1. race_payouts から確定複勝払戻を取得（payout は 100円あたり払戻金額）
        payout_rows = (
            await db.execute(
                text("""
                    SELECT race_id, combination::int AS horse_number,
                           payout::float / 100.0 AS odds
                    FROM keiba.race_payouts
                    WHERE bet_type = 'place'
                      AND combination ~ '^[0-9]+$'
                      AND race_id = ANY(:race_ids)
                """),
                {"race_ids": race_ids},
            )
        ).fetchall()
        for pr in payout_rows:
            place_odds_map[(pr.race_id, pr.horse_number)] = float(pr.odds)
            payout_covered_race_ids.add(pr.race_id)

        # 2. race_payouts に存在しないレースは odds_history の最新オッズで補完
        missing_race_ids = [
            rid for rid in race_ids
            if rid not in payout_covered_race_ids
        ]
        if missing_race_ids:
            fallback_rows = (
                await db.execute(
                    text("""
                        SELECT race_id, combination::int AS horse_number, odds
                        FROM (
                            SELECT race_id, combination, odds,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY race_id, combination
                                       ORDER BY fetched_at DESC
                                   ) AS rn
                            FROM keiba.odds_history
                            WHERE bet_type = 'place'
                              AND combination ~ '^[0-9]+$'
                              AND race_id = ANY(:race_ids)
                        ) t
                        WHERE rn = 1
                    """),
                    {"race_ids": missing_race_ids},
                )
            ).fetchall()
            for pr in fallback_rows:
                place_odds_map[(pr.race_id, pr.horse_number)] = float(pr.odds)

    # --- レースごとの指標を計算 ---
    race_metrics: list[dict] = []

    for race_id, horses in race_groups.items():
        # 条件フィルタ（Python側）
        sample = horses[0]
        cond_label = _condition_label(
            sample.grade, sample.race_type_code, sample.prize_1st
        )
        if conditions and cond_label not in conditions:
            continue

        composite_indices = [
            float(h.composite_index)
            for h in horses
            if h.composite_index is not None
        ]
        if not composite_indices:
            continue

        conf = calculate_race_confidence(composite_indices, sample.head_count)

        valid = [
            h
            for h in horses
            if h.composite_index is not None and h.finish_position is not None
        ]
        if not valid:
            continue

        sorted_by_pred = sorted(
            valid, key=lambda h: float(h.composite_index), reverse=True
        )
        predicted_winner = sorted_by_pred[0]
        predicted_top3_ids = {h.horse_id for h in sorted_by_pred[:3]}

        win_pos = int(predicted_winner.finish_position)
        win_hit = win_pos == 1
        place_hit = win_pos <= 3

        actual_top3_ids = {
            h.horse_id for h in valid if int(h.finish_position) <= 3
        }
        coverage = (
            len(actual_top3_ids & predicted_top3_ids) / max(len(actual_top3_ids), 1)
            if actual_top3_ids
            else 0.0
        )

        roi_contribution_win = (
            float(predicted_winner.win_odds) * 100.0
            if win_hit and predicted_winner.win_odds
            else 0.0
        )

        horse_number = (
            int(predicted_winner.horse_number)
            if predicted_winner.horse_number is not None
            else None
        )
        place_odds_val = (
            place_odds_map.get((race_id, horse_number))
            if horse_number is not None
            else None
        )
        # race_payouts カバー済み（結果確定）のレースは着外でも「賭けた」扱いにする。
        # race_payouts に予測 1 位馬のエントリがない = 着外 = 回収 0 円（ROI 計算上正しい）。
        has_place_odds = race_id in payout_covered_race_ids or place_odds_val is not None
        roi_contribution_place = (
            place_odds_val * 100.0
            if place_hit and place_odds_val is not None
            else 0.0
        )

        race_metrics.append({
            "race_date": str(sample.race_date),
            "confidence_label": conf["label"],
            "course_name": str(sample.course_name or "不明"),
            "surface": _normalize_surface(sample.surface),
            "distance_range": _distance_range_label(sample.distance),
            "condition": cond_label,
            "win_hit": win_hit,
            "place_hit": place_hit,
            "top3_coverage": coverage,
            "roi_contribution_win": roi_contribution_win,
            "roi_contribution_place": roi_contribution_place,
            "has_place_odds": has_place_odds,
        })

    # --- 空データ処理 ---
    if not race_metrics:
        return PerformanceSummaryOut(
            from_date=from_date,
            to_date=to_date,
            total_races=0,
            win_hit_rate=0.0,
            place_hit_rate=0.0,
            top3_coverage_rate=0.0,
            simulated_roi_win=0.0,
            simulated_roi_place=0.0,
            place_roi_races=0,
            breakdown={"HIGH": None, "MID": None, "LOW": None},
            monthly_stats=[],
            by_course=[],
            by_surface=[],
            by_distance_range=[],
            by_condition=[],
        )

    # --- 全体集計 ---
    overall = _agg(race_metrics)
    assert overall is not None

    # --- 信頼度別集計 ---
    breakdown: dict[str, ConfidenceStats | None] = {
        label: _agg([m for m in race_metrics if m["confidence_label"] == label])
        for label in ("HIGH", "MID", "LOW")
    }

    # --- 月次集計 ---
    monthly_groups: dict[str, list[dict]] = defaultdict(list)
    for m in race_metrics:
        ym = f"{m['race_date'][:4]}-{m['race_date'][4:6]}"
        monthly_groups[ym].append(m)

    monthly_stats: list[MonthlyStats] = []
    for ym in sorted(monthly_groups):
        items = monthly_groups[ym]
        agg = _agg(items)
        if not agg:
            continue
        monthly_stats.append(
            MonthlyStats(
                year_month=ym,
                total_races=agg.total_races,
                win_hit_rate=agg.win_hit_rate,
                place_hit_rate=agg.place_hit_rate,
                top3_coverage_rate=agg.top3_coverage_rate,
                simulated_roi_win=agg.simulated_roi_win,
                simulated_roi_place=agg.simulated_roi_place,
                place_roi_races=agg.place_roi_races,
                breakdown={
                    label: _agg(
                        [i for i in items if i["confidence_label"] == label]
                    )
                    for label in ("HIGH", "MID", "LOW")
                },
            )
        )

    # --- ディメンション別集計 ---
    def _group_by(key: str) -> dict[str, list[dict]]:
        g: dict[str, list[dict]] = defaultdict(list)
        for m in race_metrics:
            g[m[key]].append(m)
        return g

    by_course = _sort_by_course_group(_group_by("course_name"))
    by_surface = _dim_stats(_group_by("surface"), order=_SURFACE_ORDER)
    by_distance_range = _dim_stats(_group_by("distance_range"), order=_DISTANCE_ORDER)
    by_condition = _dim_stats(_group_by("condition"), order=_CONDITION_ORDER)

    return PerformanceSummaryOut(
        from_date=from_date,
        to_date=to_date,
        total_races=overall.total_races,
        win_hit_rate=overall.win_hit_rate,
        place_hit_rate=overall.place_hit_rate,
        top3_coverage_rate=overall.top3_coverage_rate,
        simulated_roi_win=overall.simulated_roi_win,
        simulated_roi_place=overall.simulated_roi_place,
        place_roi_races=overall.place_roi_races,
        breakdown=breakdown,
        monthly_stats=monthly_stats,
        by_course=by_course,
        by_surface=by_surface,
        by_distance_range=by_distance_range,
        by_condition=by_condition,
    )


# ---------------------------------------------------------------------------
# オッズ感度分析用エンドポイント
# ---------------------------------------------------------------------------


class OddsDataPoint(BaseModel):
    """オッズ感度分析用の1レース分データ。"""

    win_odds: float | None      # RaceResult.win_odds（単勝オッズ）
    win_hit: bool               # 予測1位馬が1着
    place_odds: float | None    # 複勝オッズ（race_payouts優先, odds_historyフォールバック）
    place_hit: bool             # 予測1位馬が3着以内
    has_place_odds: bool        # race_payoutsカバー済み or odds_historyあり


@router.get("/odds-data", response_model=list[OddsDataPoint])
async def get_odds_data(
    db: DbDep,
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    course_name: str | None = Query(default=None),
    surface: str | None = Query(default=None),
    distance_range: str | None = Query(default=None),
    condition: str | None = Query(default=None),
) -> list[OddsDataPoint]:
    """各レースの予測1位馬のオッズと的中データを返す（オッズ感度分析用）。"""
    # --- デフォルト日付（今月1日〜今日）---
    today = _date.today()
    if from_date is None:
        from_date = f"{today.year}{str(today.month).zfill(2)}01"
    if to_date is None:
        to_date = today.strftime("%Y%m%d")

    # --- カンマ区切りパラメータを展開 ---
    def _split(v: str | None) -> list[str] | None:
        if not v:
            return None
        items = [s.strip() for s in v.split(",") if s.strip()]
        return items if items else None

    course_names = _split(course_name)
    surfaces = _split(surface)
    distance_ranges = _split(distance_range)
    conditions = _split(condition)

    # --- 各 (race_id, horse_id) の最新バージョンサブクエリ ---
    latest_version_sq = (
        select(
            CalculatedIndex.race_id.label("lv_race_id"),
            CalculatedIndex.horse_id.label("lv_horse_id"),
            func.max(CalculatedIndex.version).label("lv_max_version"),
        )
        .group_by(CalculatedIndex.race_id, CalculatedIndex.horse_id)
        .subquery()
    )

    # --- SQLフィルタ条件の構築 ---
    sql_conditions = [
        Race.date >= from_date,
        Race.date <= to_date,
        RaceResult.finish_position.is_not(None),
        Race.course.in_(list(_JRA_COURSE_CODES)),
    ]
    if course_names:
        sql_conditions.append(Race.course_name.in_(course_names))
    if surfaces:
        sql_conditions.append(Race.surface.in_(surfaces))
    if distance_ranges:
        dist_conds = []
        for dr in distance_ranges:
            entry = _DISTANCE_KEY_MAP.get(dr) or _DISTANCE_KEY_MAP.get(
                _DISTANCE_LABEL_TO_KEY.get(dr, ""), None
            )
            if entry:
                _, lo, hi = entry
                dist_conds.append((Race.distance >= lo) & (Race.distance <= hi))
        if dist_conds:
            sql_conditions.append(or_(*dist_conds))

    # --- 全馬 composite_index + 着順を一括取得 ---
    rows = (
        await db.execute(
            select(
                Race.id.label("race_id"),
                Race.grade.label("grade"),
                Race.race_type_code.label("race_type_code"),
                Race.prize_1st.label("prize_1st"),
                CalculatedIndex.horse_id,
                CalculatedIndex.composite_index,
                RaceResult.finish_position,
                RaceResult.horse_number,
                RaceResult.win_odds,
            )
            .join(CalculatedIndex, CalculatedIndex.race_id == Race.id)
            .join(
                latest_version_sq,
                (latest_version_sq.c.lv_race_id == CalculatedIndex.race_id)
                & (latest_version_sq.c.lv_horse_id == CalculatedIndex.horse_id)
                & (latest_version_sq.c.lv_max_version == CalculatedIndex.version),
            )
            .join(
                RaceResult,
                (RaceResult.race_id == Race.id)
                & (RaceResult.horse_id == CalculatedIndex.horse_id),
            )
            .where(*sql_conditions)
            .order_by(Race.date, Race.id)
        )
    ).fetchall()

    # --- レースごとにグループ化 ---
    race_groups: dict[int, list] = defaultdict(list)
    for row in rows:
        race_groups[row.race_id].append(row)

    # --- 複勝オッズを race_payouts → odds_history の順で一括取得 ---
    race_ids = list(race_groups.keys())
    place_odds_map: dict[tuple[int, int], float] = {}
    payout_covered_race_ids: set[int] = set()
    if race_ids:
        payout_rows = (
            await db.execute(
                text("""
                    SELECT race_id, combination::int AS horse_number,
                           payout::float / 100.0 AS odds
                    FROM keiba.race_payouts
                    WHERE bet_type = 'place'
                      AND combination ~ '^[0-9]+$'
                      AND race_id = ANY(:race_ids)
                """),
                {"race_ids": race_ids},
            )
        ).fetchall()
        for pr in payout_rows:
            place_odds_map[(pr.race_id, pr.horse_number)] = float(pr.odds)
            payout_covered_race_ids.add(pr.race_id)

        missing_race_ids = [
            rid for rid in race_ids
            if rid not in payout_covered_race_ids
        ]
        if missing_race_ids:
            fallback_rows = (
                await db.execute(
                    text("""
                        SELECT race_id, combination::int AS horse_number, odds
                        FROM (
                            SELECT race_id, combination, odds,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY race_id, combination
                                       ORDER BY fetched_at DESC
                                   ) AS rn
                            FROM keiba.odds_history
                            WHERE bet_type = 'place'
                              AND combination ~ '^[0-9]+$'
                              AND race_id = ANY(:race_ids)
                        ) t
                        WHERE rn = 1
                    """),
                    {"race_ids": missing_race_ids},
                )
            ).fetchall()
            for pr in fallback_rows:
                place_odds_map[(pr.race_id, pr.horse_number)] = float(pr.odds)

    # --- レースごとに予測1位馬のデータを抽出 ---
    result: list[OddsDataPoint] = []

    for race_id, horses in race_groups.items():
        # 条件フィルタ（Python側）
        sample = horses[0]
        cond_label = _condition_label(
            sample.grade, sample.race_type_code, sample.prize_1st
        )
        if conditions and cond_label not in conditions:
            continue

        valid = [
            h
            for h in horses
            if h.composite_index is not None and h.finish_position is not None
        ]
        if not valid:
            continue

        sorted_by_pred = sorted(
            valid, key=lambda h: float(h.composite_index), reverse=True
        )
        predicted_winner = sorted_by_pred[0]

        win_pos = int(predicted_winner.finish_position)
        win_hit = win_pos == 1
        place_hit = win_pos <= 3

        win_odds_val = (
            float(predicted_winner.win_odds)
            if predicted_winner.win_odds is not None
            else None
        )

        horse_number = (
            int(predicted_winner.horse_number)
            if predicted_winner.horse_number is not None
            else None
        )
        place_odds_val = (
            place_odds_map.get((race_id, horse_number))
            if horse_number is not None
            else None
        )
        has_place_odds = race_id in payout_covered_race_ids or place_odds_val is not None

        result.append(
            OddsDataPoint(
                win_odds=win_odds_val,
                win_hit=win_hit,
                place_odds=place_odds_val,
                place_hit=place_hit,
                has_place_odds=has_place_odds,
            )
        )

    return result
