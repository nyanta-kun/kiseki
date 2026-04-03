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
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CalculatedIndex, Race, RaceResult
from ..db.session import get_db
from ..indices.composite import COMPOSITE_VERSION
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

    # --- SQLフィルタ条件の構築 ---
    sql_conditions = [
        CalculatedIndex.version == COMPOSITE_VERSION,
        Race.date >= from_date,
        Race.date <= to_date,
        RaceResult.finish_position.is_not(None),
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

    # --- 複勝オッズを odds_history から一括取得 ---
    race_ids = list(race_groups.keys())
    place_odds_map: dict[tuple[int, int], float] = {}
    if race_ids:
        place_rows = (
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
                {"race_ids": race_ids},
            )
        ).fetchall()
        for pr in place_rows:
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
        has_place_odds = place_odds_val is not None
        roi_contribution_place = (
            place_odds_val * 100.0
            if place_hit and place_odds_val is not None
            else 0.0
        )

        race_metrics.append({
            "race_date": str(sample.race_date),
            "confidence_label": conf["label"],
            "course_name": str(sample.course_name or "不明"),
            "surface": str(sample.surface or "不明"),
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

    by_course = _dim_stats(_group_by("course_name"), sort_by_races=True)
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
