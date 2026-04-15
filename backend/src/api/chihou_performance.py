"""地方競馬 AI指数精度実績 API

ChihouCalculatedIndex の composite_index 予測 vs ChihouRaceResult の実際着順を集計し、
指数モデルの予測精度（的中率・回収率シミュレーション）を返す。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

router = APIRouter(prefix="/api/chihou/performance", tags=["chihou-performance"])
DbDep = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# レスポンスモデル（JRA 版と互換形式）
# ---------------------------------------------------------------------------

class ChihouMonthlyStats(BaseModel):
    """月次成績集計。"""

    year_month: str
    total_races: int
    win_hit_rate: float
    place_hit_rate: float
    top3_coverage_rate: float
    simulated_roi_win: float
    simulated_roi_place: float
    place_roi_races: int


class ChihouDimensionStat(BaseModel):
    """ディメンション別成績集計。"""

    label: str
    total_races: int
    win_hit_rate: float
    place_hit_rate: float
    top3_coverage_rate: float
    simulated_roi_win: float
    simulated_roi_place: float
    place_roi_races: int


class ChihouPerformanceSummaryOut(BaseModel):
    """地方競馬 AI指数精度実績サマリーレスポンス。"""

    from_date: str
    to_date: str
    total_races: int
    win_hit_rate: float
    place_hit_rate: float
    top3_coverage_rate: float
    simulated_roi_win: float
    simulated_roi_place: float
    place_roi_races: int
    monthly_stats: list[ChihouMonthlyStats]
    by_course: list[ChihouDimensionStat]
    by_surface: list[ChihouDimensionStat]


# ---------------------------------------------------------------------------
# 集計ヘルパー
# ---------------------------------------------------------------------------

def _agg(items: list[dict]) -> dict:
    """レース指標リストから集計値を算出する。"""
    total = len(items)
    if total == 0:
        return {
            "total_races": 0,
            "win_hit_rate": 0.0,
            "place_hit_rate": 0.0,
            "top3_coverage_rate": 0.0,
            "simulated_roi_win": 0.0,
            "simulated_roi_place": 0.0,
            "place_roi_races": 0,
        }
    place_items = [i for i in items if i["has_place_odds"]]
    place_total = len(place_items)
    return {
        "total_races": total,
        "win_hit_rate": round(sum(1 for i in items if i["win_hit"]) / total, 4),
        "place_hit_rate": round(sum(1 for i in items if i["place_hit"]) / total, 4),
        "top3_coverage_rate": round(sum(i["top3_coverage"] for i in items) / total, 4),
        "simulated_roi_win": round(
            sum(i["roi_win"] for i in items) / (total * 100), 4
        ),
        "simulated_roi_place": round(
            sum(i["roi_place"] for i in place_items) / (place_total * 100), 4
        ) if place_total else 0.0,
        "place_roi_races": place_total,
    }


# ---------------------------------------------------------------------------
# クエリ
# ---------------------------------------------------------------------------

_SUMMARY_SQL = """
WITH
race_top1 AS (
    SELECT DISTINCT ON (ci.race_id)
        ci.race_id, ci.horse_id
    FROM chihou.calculated_indices ci
    WHERE ci.version = :version
    ORDER BY ci.race_id, ci.composite_index DESC NULLS LAST
),
race_top3 AS (
    SELECT sub.race_id, array_agg(sub.horse_id) AS pred_ids
    FROM (
        SELECT race_id, horse_id,
               ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY composite_index DESC NULLS LAST) AS rn
        FROM chihou.calculated_indices
        WHERE version = :version
    ) sub
    WHERE rn <= 3
    GROUP BY sub.race_id
),
actual_1st AS (
    SELECT rr.race_id, rr.horse_id
    FROM chihou.race_results rr
    WHERE rr.finish_position = 1
),
actual_top3 AS (
    SELECT rr.race_id, array_agg(rr.horse_id) AS actual_ids
    FROM chihou.race_results rr
    WHERE rr.finish_position IS NOT NULL AND rr.finish_position <= 3
    GROUP BY rr.race_id
),
entry_nums AS (
    SELECT e.race_id, e.horse_id, e.horse_number
    FROM chihou.race_entries e
),
latest_win_odds AS (
    SELECT DISTINCT ON (oh.race_id, oh.combination)
        oh.race_id, oh.combination AS horse_num_str, oh.odds
    FROM chihou.odds_history oh
    WHERE oh.bet_type = 'win'
    ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC
),
latest_place_odds AS (
    SELECT DISTINCT ON (oh.race_id, oh.combination)
        oh.race_id, oh.combination AS horse_num_str, oh.odds
    FROM chihou.odds_history oh
    WHERE oh.bet_type = 'place'
    ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC
)
SELECT
    r.date,
    r.course_name,
    r.surface,
    r.distance,
    t1.horse_id  AS pred_1st,
    t3.pred_ids,
    a1.horse_id  AS actual_1st,
    at3.actual_ids,
    wo.odds      AS win_odds,
    po.odds      AS place_odds
FROM race_top1 t1
JOIN chihou.races r ON r.id = t1.race_id
JOIN race_top3 t3 ON t3.race_id = t1.race_id
LEFT JOIN actual_1st a1 ON a1.race_id = t1.race_id
LEFT JOIN actual_top3 at3 ON at3.race_id = t1.race_id
LEFT JOIN entry_nums en ON en.race_id = t1.race_id AND en.horse_id = t1.horse_id
LEFT JOIN latest_win_odds wo
    ON wo.race_id = t1.race_id AND wo.horse_num_str = en.horse_number::text
LEFT JOIN latest_place_odds po
    ON po.race_id = t1.race_id AND po.horse_num_str = en.horse_number::text
WHERE r.date >= :from_date
  AND r.date <= :to_date
  AND a1.horse_id IS NOT NULL
ORDER BY r.date
"""


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------

@router.get("/summary")
async def get_chihou_performance_summary(
    from_date: str = Query(..., description="集計開始日 YYYYMMDD"),
    to_date: str = Query(..., description="集計終了日 YYYYMMDD"),
    course_name: str | None = Query(None, description="競馬場名（カンマ区切り複数可）"),
    surface: str | None = Query(None, description="馬場（芝/ダ、カンマ区切り複数可）"),
    db: AsyncSession = Depends(get_db),
) -> ChihouPerformanceSummaryOut:
    """地方競馬 AI指数の予測精度・的中率・回収率シミュレーション実績を返す。"""
    result = await db.execute(
        sql_text(_SUMMARY_SQL),
        {"version": CHIHOU_COMPOSITE_VERSION, "from_date": from_date, "to_date": to_date},
    )
    rows = result.fetchall()

    # クライアント側フィルタ（course_name / surface）
    course_filter = [c.strip() for c in course_name.split(",")] if course_name else []
    surface_filter = [s.strip() for s in surface.split(",")] if surface else []

    items: list[dict] = []
    for row in rows:
        (
            date, cname, surf, dist,
            pred_1st, pred_ids,
            actual_1st, actual_ids,
            win_odds, place_odds,
        ) = row

        if course_filter and cname not in course_filter:
            continue
        if surface_filter and surf not in surface_filter:
            continue

        pred_set = set(pred_ids or [])
        actual_set = set(actual_ids or [])

        win_hit = pred_1st is not None and pred_1st == actual_1st
        place_hit = pred_1st is not None and pred_1st in actual_set
        top3_coverage = (
            len(pred_set & actual_set) / max(1, len(actual_set))
            if actual_set else 0.0
        )
        win_odds_f = float(win_odds) if win_odds is not None else None
        place_odds_f = float(place_odds) if place_odds is not None else None

        items.append({
            "date": date,
            "course_name": cname,
            "surface": surf,
            "win_hit": win_hit,
            "place_hit": place_hit,
            "top3_coverage": top3_coverage,
            "roi_win": win_odds_f * 100 if win_hit and win_odds_f else 0.0,
            "roi_place": place_odds_f * 100 if place_hit and place_odds_f else 0.0,
            "has_place_odds": place_odds_f is not None,
        })

    # 全体集計
    agg = _agg(items)

    # 月次集計
    monthly: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        ym = i["date"][:6]  # YYYYMM
        monthly[ym].append(i)

    monthly_stats = [
        ChihouMonthlyStats(year_month=ym, **_agg(v))
        for ym, v in sorted(monthly.items())
    ]

    # 競馬場別
    by_course_groups: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        by_course_groups[i["course_name"]].append(i)

    by_course = sorted(
        [
            ChihouDimensionStat(label=label, **_agg(v))
            for label, v in by_course_groups.items()
        ],
        key=lambda x: x.total_races,
        reverse=True,
    )

    # 馬場別
    by_surface_groups: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        by_surface_groups[i["surface"] or "不明"].append(i)

    by_surface = sorted(
        [
            ChihouDimensionStat(label=label, **_agg(v))
            for label, v in by_surface_groups.items()
        ],
        key=lambda x: x.total_races,
        reverse=True,
    )

    return ChihouPerformanceSummaryOut(
        from_date=from_date,
        to_date=to_date,
        **agg,
        monthly_stats=monthly_stats,
        by_course=by_course,
        by_surface=by_surface,
    )


# ---------------------------------------------------------------------------
# 購入指針 統計エンドポイント
# ---------------------------------------------------------------------------

class ChihouBuyingGuideRow(BaseModel):
    """地方購入指針分析の1行。"""

    label: str
    races: int
    win_pct: float
    place_pct: float
    win_roi: float


class ChihouBuyingGuide(BaseModel):
    """地方競馬購入指針統計レスポンス。"""

    odds_cutoff: list[ChihouBuyingGuideRow]
    by_course: list[ChihouBuyingGuideRow]
    by_distance: list[ChihouBuyingGuideRow]
    since: str


@router.get("/buying-guide", response_model=ChihouBuyingGuide)
async def get_chihou_buying_guide(
    db: DbDep,
    since: str = Query("20250101", description="集計開始日 YYYYMMDD"),
    version: int = Query(CHIHOU_COMPOSITE_VERSION, description="指数バージョン"),
) -> ChihouBuyingGuide:
    """地方競馬購入指針統計（オッズ別・競馬場別・距離帯別）を返す。"""

    params = {"since": since, "ver": version}

    # ── オッズ別 ──────────────────────────────────────────────────────────
    odds_sql = sql_text("""
        WITH base AS (
            SELECT rr.finish_position, rr.win_odds,
                RANK() OVER (PARTITION BY r.id ORDER BY ci.composite_index DESC) AS ci_rank
            FROM chihou.calculated_indices ci
            JOIN chihou.races r ON r.id = ci.race_id
            JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
            WHERE r.date >= :since AND ci.version = :ver
              AND rr.abnormality_code = 0 AND rr.finish_position IS NOT NULL
        ),
        top1 AS (SELECT finish_position, win_odds FROM base WHERE ci_rank = 1)
        SELECT label, ord,
            COUNT(*) AS races,
            ROUND(AVG(CASE WHEN finish_position=1 THEN 1.0 ELSE 0 END)*100,1),
            ROUND(AVG(CASE WHEN finish_position<=3 THEN 1.0 ELSE 0 END)*100,1),
            ROUND(SUM(CASE WHEN finish_position=1 THEN win_odds ELSE 0 END)/NULLIF(COUNT(*),0)*100,1)
        FROM (
            SELECT finish_position, win_odds, '全体' AS label, 0 AS ord FROM top1
            UNION ALL SELECT finish_position, win_odds, '2.0倍以上', 1 FROM top1 WHERE win_odds >= 2.0
            UNION ALL SELECT finish_position, win_odds, '3.0倍以上', 2 FROM top1 WHERE win_odds >= 3.0
            UNION ALL SELECT finish_position, win_odds, '4.0倍以上', 3 FROM top1 WHERE win_odds >= 4.0
            UNION ALL SELECT finish_position, win_odds, '6.0倍以上', 4 FROM top1 WHERE win_odds >= 6.0
            UNION ALL SELECT finish_position, win_odds, '10.0倍以上', 5 FROM top1 WHERE win_odds >= 10.0
        ) t GROUP BY label, ord ORDER BY ord
    """)

    # ── 競馬場別 ──────────────────────────────────────────────────────────
    course_sql = sql_text("""
        WITH top1 AS (
            SELECT r.course_name AS label,
                rr.finish_position, rr.win_odds,
                RANK() OVER (PARTITION BY r.id ORDER BY ci.composite_index DESC) AS ci_rank
            FROM chihou.calculated_indices ci
            JOIN chihou.races r ON r.id = ci.race_id
            JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
            WHERE r.date >= :since AND ci.version = :ver
              AND rr.abnormality_code = 0 AND rr.finish_position IS NOT NULL
        )
        SELECT label, COUNT(*),
            ROUND(AVG(CASE WHEN finish_position=1 THEN 1.0 ELSE 0 END)*100,1),
            ROUND(AVG(CASE WHEN finish_position<=3 THEN 1.0 ELSE 0 END)*100,1),
            ROUND(SUM(CASE WHEN finish_position=1 THEN win_odds ELSE 0 END)/NULLIF(COUNT(*),0)*100,1)
        FROM top1 WHERE ci_rank = 1
        GROUP BY label ORDER BY 5 DESC
    """)

    # ── 距離帯別 ──────────────────────────────────────────────────────────
    dist_sql = sql_text("""
        WITH top1 AS (
            SELECT
                CASE
                    WHEN r.distance <= 1400 THEN '短距離(〜1400m)'
                    WHEN r.distance <= 1800 THEN 'マイル(1401-1800m)'
                    WHEN r.distance <= 2200 THEN '中距離(1801-2200m)'
                    ELSE '長距離(2201m〜)'
                END AS label,
                CASE
                    WHEN r.distance <= 1400 THEN 1
                    WHEN r.distance <= 1800 THEN 2
                    WHEN r.distance <= 2200 THEN 3
                    ELSE 4
                END AS ord,
                rr.finish_position, rr.win_odds,
                RANK() OVER (PARTITION BY r.id ORDER BY ci.composite_index DESC) AS ci_rank
            FROM chihou.calculated_indices ci
            JOIN chihou.races r ON r.id = ci.race_id
            JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
            WHERE r.date >= :since AND ci.version = :ver
              AND rr.abnormality_code = 0 AND rr.finish_position IS NOT NULL
        )
        SELECT label, ord, COUNT(*),
            ROUND(AVG(CASE WHEN finish_position=1 THEN 1.0 ELSE 0 END)*100,1),
            ROUND(AVG(CASE WHEN finish_position<=3 THEN 1.0 ELSE 0 END)*100,1),
            ROUND(SUM(CASE WHEN finish_position=1 THEN win_odds ELSE 0 END)/NULLIF(COUNT(*),0)*100,1)
        FROM top1 WHERE ci_rank = 1
        GROUP BY label, ord ORDER BY ord
    """)

    odds_result = (await db.execute(odds_sql, params)).all()
    course_result = (await db.execute(course_sql, params)).all()
    dist_result = (await db.execute(dist_sql, params)).all()

    # odds_sql returns: label, ord, races, win_pct, place_pct, win_roi
    odds_rows = [
        ChihouBuyingGuideRow(
            label=str(r[0]), races=int(r[2]),
            win_pct=float(r[3]), place_pct=float(r[4]), win_roi=float(r[5])
        )
        for r in odds_result if int(r[2]) >= 20
    ]
    # course_sql returns: label, races, win_pct, place_pct, win_roi
    course_rows = [
        ChihouBuyingGuideRow(
            label=str(r[0]), races=int(r[1]),
            win_pct=float(r[2]), place_pct=float(r[3]), win_roi=float(r[4])
        )
        for r in course_result if int(r[1]) >= 20
    ]
    # dist_sql returns: label, ord, races, win_pct, place_pct, win_roi
    dist_rows = [
        ChihouBuyingGuideRow(
            label=str(r[0]), races=int(r[2]),
            win_pct=float(r[3]), place_pct=float(r[4]), win_roi=float(r[5])
        )
        for r in dist_result if int(r[2]) >= 20
    ]

    return ChihouBuyingGuide(
        odds_cutoff=odds_rows,
        by_course=course_rows,
        by_distance=dist_rows,
        since=since,
    )
