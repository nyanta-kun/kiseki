"""指数傾向分析スクリプト

指数上位が3着以内に来なかったケース（上振れ外れ）と
指数6位以下が3着以内に来たケース（下振れ穴馬）の傾向を分析する。

使い方:
  uv run python scripts/index_pattern_analysis.py --year 2026
  uv run python scripts/index_pattern_analysis.py --year 2025
  uv run python scripts/index_pattern_analysis.py --year 2025 --from-month 1 --to-month 9
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from itertools import groupby
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import text

from src.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


def fmt(label: str, width: int = 62) -> str:
    return f"\n{'─'*width}\n{label}\n{'─'*width}"


async def rq(session, sql: str, params: dict) -> list:
    result = await session.execute(text(sql), params)
    return result.fetchall()


# ---------------------------------------------------------------------------
# ベースCTE（全分析共通）
# ---------------------------------------------------------------------------

def base_cte(month_filter: str) -> str:
    return f"""
WITH race_size AS (
    SELECT race_id, COUNT(*) AS entry_count
    FROM keiba.race_entries GROUP BY race_id
),
ranked AS (
    SELECT
        ci.race_id,
        ci.horse_id,
        ci.composite_index,
        ci.speed_index,
        ci.last_3f_index,
        ci.jockey_index,
        ci.pace_index,
        ci.course_aptitude,
        ci.rotation_index,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.speed_index DESC)     AS speed_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.last_3f_index DESC)   AS last3f_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.jockey_index DESC)    AS jockey_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.pace_index DESC)      AS pace_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.course_aptitude DESC) AS course_rank,
        r.date,
        r.surface,
        r.distance,
        r.course_name,
        COALESCE(r.grade, '一般') AS grade,
        rs.entry_count,
        rr.finish_position,
        rr.win_odds
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    JOIN race_size rs ON rs.race_id = ci.race_id
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
        AND rr.abnormality_code = 0
    WHERE LEFT(r.date, 4) = :year
      {month_filter}
      AND rs.entry_count >= 6
      AND rr.finish_position IS NOT NULL
)
"""


# ---------------------------------------------------------------------------
# 1. サマリー
# ---------------------------------------------------------------------------

async def print_summary(session, params: dict, mf: str, period: str) -> None:
    sql = base_cte(mf) + """
SELECT
    COUNT(DISTINCT race_id) AS total_races,
    COUNT(*) AS total_horses,
    ROUND(AVG(entry_count)::numeric,1) AS avg_field,
    COUNT(CASE WHEN idx_rank=1 THEN 1 END) AS r1_cnt,
    COUNT(CASE WHEN idx_rank=1 AND finish_position=1 THEN 1 END) AS r1_win,
    COUNT(CASE WHEN idx_rank=1 AND finish_position<=3 THEN 1 END) AS r1_place,
    COUNT(CASE WHEN idx_rank<=3 THEN 1 END) AS top3_cnt,
    COUNT(CASE WHEN idx_rank<=3 AND finish_position<=3 THEN 1 END) AS top3_place,
    COUNT(CASE WHEN idx_rank>=6 THEN 1 END) AS low_cnt,
    COUNT(CASE WHEN idx_rank>=6 AND finish_position<=3 THEN 1 END) AS low_place
FROM ranked
"""
    rows = await rq(session, sql, params)
    r = rows[0]
    if not r.r1_cnt:
        print("  ⚠ データなし（指数算出未完了の可能性）")
        return

    r1_win_pct   = r.r1_win   / r.r1_cnt   * 100
    r1_place_pct = r.r1_place / r.r1_cnt   * 100
    top3_rate    = r.top3_place / r.top3_cnt * 100 if r.top3_cnt else 0
    low_rate     = r.low_place  / r.low_cnt  * 100 if r.low_cnt  else 0
    random_place = 3.0 / float(r.avg_field) * 100 if r.avg_field else 0

    print(fmt(f"■ 全体サマリー ({period})"))
    print(f"  対象レース数  : {r.total_races:,}")
    print(f"  平均頭数      : {r.avg_field}")
    print(f"  ランダム3着率 : {random_place:.1f}%")
    print()
    print(f"  【指数1位】  勝率 {r.r1_win}/{r.r1_cnt} = {r1_win_pct:.1f}%  "
          f"3着内率 {r.r1_place}/{r.r1_cnt} = {r1_place_pct:.1f}%")
    print(f"  【指数Top3】 3着内的中率 {r.top3_place}/{r.top3_cnt} = {top3_rate:.1f}%")
    print(f"  【指数6位以下】3着内混入率 {r.low_place}/{r.low_cnt} = {low_rate:.1f}%")


# ---------------------------------------------------------------------------
# 2. ランク別精度
# ---------------------------------------------------------------------------

async def print_rank_accuracy(session, params: dict, mf: str) -> None:
    sql = base_cte(mf) + """
SELECT
    idx_rank,
    COUNT(*) AS cnt,
    COUNT(CASE WHEN finish_position=1 THEN 1 END) AS wins,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS places,
    ROUND(COUNT(CASE WHEN finish_position=1 THEN 1 END)::numeric/COUNT(*)*100,1) AS win_pct,
    ROUND(COUNT(CASE WHEN finish_position<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS place_pct,
    ROUND(AVG(finish_position)::numeric,2) AS avg_pos
FROM ranked
WHERE idx_rank <= 12
GROUP BY idx_rank
ORDER BY idx_rank
"""
    rows = await rq(session, sql, params)
    print(fmt("■ 指数ランク別 精度一覧"))
    print(f"  {'ランク':>5} {'頭数':>6} {'勝率':>7} {'3着内率':>8} {'平均着順':>9}")
    for r in rows:
        bar = "█" * int(float(r.place_pct or 0) / 5)
        print(f"  {r.idx_rank:>4}位 {r.cnt:>6} {(r.win_pct or 0):>6.1f}% "
              f"{(r.place_pct or 0):>7.1f}% {r.avg_pos:>8.2f} {bar}")


# ---------------------------------------------------------------------------
# 3. 上位外れ分析
# ---------------------------------------------------------------------------

async def print_miss_patterns(session, params: dict, mf: str) -> None:
    print(fmt("■ 上位外れ分析（指数1〜3位 → 4着以下）"))

    # 馬場×距離
    sql = base_cte(mf) + """
SELECT
    surface,
    CASE
        WHEN distance < 1400 THEN '~1399m'
        WHEN distance < 1800 THEN '1400~1799m'
        WHEN distance < 2200 THEN '1800~2199m'
        ELSE '2200m+'
    END AS dist_grp,
    COUNT(*) AS top3_cnt,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS placed,
    ROUND(COUNT(CASE WHEN finish_position<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS place_pct,
    ROUND(AVG(finish_position)::numeric,2) AS avg_pos
FROM ranked
WHERE idx_rank <= 3
GROUP BY surface, dist_grp
ORDER BY surface, dist_grp
"""
    rows = await rq(session, sql, params)
    print(f"\n  【馬場×距離別 3着内率】")
    print(f"  {'馬場':<4} {'距離帯':<13} {'Top3頭数':>8} {'3着内':>6} {'3着内率':>8} {'平均着順':>8}")
    for r in rows:
        flag = " ◀低" if float(r.place_pct or 0) < 30 else ""
        print(f"  {r.surface:<4} {r.dist_grp:<13} {r.top3_cnt:>8} "
              f"{r.placed:>6} {(r.place_pct or 0):>7.1f}% {r.avg_pos:>8.2f}{flag}")

    # 頭数別
    sql = base_cte(mf) + """
SELECT
    entry_count,
    COUNT(*) AS top3_cnt,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS placed,
    ROUND(COUNT(CASE WHEN finish_position<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS place_pct
FROM ranked
WHERE idx_rank <= 3
GROUP BY entry_count
ORDER BY entry_count
"""
    rows = await rq(session, sql, params)
    # 頭数グループにまとめる
    groups = {}
    for r in rows:
        if r.entry_count <= 8:     g = "~8頭"
        elif r.entry_count <= 12:  g = "9~12頭"
        elif r.entry_count <= 16:  g = "13~16頭"
        else:                       g = "17頭~"
        if g not in groups:
            groups[g] = {"cnt": 0, "placed": 0,
                         "min": r.entry_count, "max": r.entry_count}
        groups[g]["cnt"] += r.top3_cnt
        groups[g]["placed"] += r.placed
        groups[g]["min"] = min(groups[g]["min"], r.entry_count)
        groups[g]["max"] = max(groups[g]["max"], r.entry_count)

    print(f"\n  【頭数別 3着内率】")
    print(f"  {'グループ':<10} {'頭数範囲':>8} {'Top3頭数':>8} {'3着内':>6} {'3着内率':>8}")
    for g in ["~8頭", "9~12頭", "13~16頭", "17頭~"]:
        if g not in groups:
            continue
        d = groups[g]
        pct = d["placed"] / d["cnt"] * 100 if d["cnt"] else 0
        flag = " ◀低" if pct < 28 else ""
        heads = f"{d['min']}~{d['max']}"
        print(f"  {g:<10} {heads:>8} {d['cnt']:>8} {d['placed']:>6} {pct:>7.1f}%{flag}")

    # グレード別
    sql = base_cte(mf) + """
SELECT
    CASE
        WHEN grade IN ('G1','G2','G3') THEN grade
        WHEN grade ILIKE '%OP%' OR grade ILIKE '%オープン%'
             OR grade ILIKE '%%L%%' THEN 'OP/L'
        WHEN surface = '障' THEN '障害'
        ELSE '一般'
    END AS grade_grp,
    COUNT(*) AS top3_cnt,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS placed,
    ROUND(COUNT(CASE WHEN finish_position<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS place_pct,
    ROUND(AVG(win_odds)::numeric,1) AS avg_odds
FROM ranked
WHERE idx_rank <= 3
GROUP BY grade_grp
ORDER BY place_pct
"""
    rows = await rq(session, sql, params)
    print(f"\n  【グレード別 3着内率】")
    print(f"  {'格':<8} {'Top3頭数':>8} {'3着内':>6} {'3着内率':>8} {'平均単オッズ':>12}")
    for r in rows:
        flag = " ◀低" if float(r.place_pct or 0) < 28 else ""
        avg_o = f"{r.avg_odds:.1f}倍" if r.avg_odds else "  N/A"
        print(f"  {r.grade_grp:<8} {r.top3_cnt:>8} {r.placed:>6} "
              f"{(r.place_pct or 0):>7.1f}% {avg_o:>11}{flag}")

    # 外れ馬のサブ指数ランク平均
    sql = base_cte(mf) + """
SELECT
    COUNT(*) AS cnt,
    ROUND(AVG(speed_rank)::numeric,2) AS avg_speed,
    ROUND(AVG(last3f_rank)::numeric,2) AS avg_last3f,
    ROUND(AVG(jockey_rank)::numeric,2) AS avg_jockey,
    ROUND(AVG(pace_rank)::numeric,2) AS avg_pace,
    ROUND(AVG(course_rank)::numeric,2) AS avg_course
FROM ranked
WHERE idx_rank <= 3 AND finish_position > 3
"""
    rows = await rq(session, sql, params)
    r = rows[0]
    print(f"\n  【外れ馬のサブ指数ランク平均】（指数Top3→4着以下 {r.cnt}頭）")
    print(f"  ※ 値が大きい = そのサブ指数では上位ではなかった")
    subs = [
        ("スピード指数", r.avg_speed),
        ("上がり3F    ", r.avg_last3f),
        ("騎手指数    ", r.avg_jockey),
        ("ペース指数  ", r.avg_pace),
        ("コース適性  ", r.avg_course),
    ]
    # ワースト（大きいほど弱点）順に表示
    subs_sorted = sorted(subs, key=lambda x: float(x[1] or 0), reverse=True)
    for name, val in subs_sorted:
        bar = "▮" * int(float(val or 0))
        worst = " ← 弱点" if float(val or 0) >= 4.5 else ""
        print(f"    {name} : {float(val or 0):>5.2f}位 {bar}{worst}")


# ---------------------------------------------------------------------------
# 4. 穴馬分析
# ---------------------------------------------------------------------------

async def print_dark_horse_patterns(session, params: dict, mf: str) -> None:
    print(fmt("■ 穴馬分析（指数6位以下 → 3着以内）"))

    # 馬場×距離
    sql = base_cte(mf) + """
SELECT
    surface,
    CASE
        WHEN distance < 1400 THEN '~1399m'
        WHEN distance < 1800 THEN '1400~1799m'
        WHEN distance < 2200 THEN '1800~2199m'
        ELSE '2200m+'
    END AS dist_grp,
    COUNT(CASE WHEN idx_rank>=6 THEN 1 END) AS low_cnt,
    COUNT(CASE WHEN idx_rank>=6 AND finish_position<=3 THEN 1 END) AS low_placed,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS total_placed,
    ROUND(
        COUNT(CASE WHEN idx_rank>=6 AND finish_position<=3 THEN 1 END)::numeric
        / NULLIF(COUNT(CASE WHEN finish_position<=3 THEN 1 END),0) * 100
    ,1) AS upset_ratio
FROM ranked
GROUP BY surface, dist_grp
ORDER BY surface, dist_grp
"""
    rows = await rq(session, sql, params)
    print(f"\n  【馬場×距離 — 3着内での穴馬割合】")
    print(f"  {'馬場':<4} {'距離帯':<13} {'6位以下':>7} {'穴3着内':>7} {'着内穴%':>8}")
    for r in rows:
        upset = float(r.upset_ratio or 0)
        flag = " ◀高" if upset > 38 else ""
        print(f"  {r.surface:<4} {r.dist_grp:<13} {r.low_cnt:>7} "
              f"{r.low_placed:>7} {upset:>7.1f}%{flag}")

    # 指数ランク6〜12位の3着内率
    sql = base_cte(mf) + """
SELECT
    idx_rank,
    COUNT(*) AS cnt,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS places,
    ROUND(COUNT(CASE WHEN finish_position<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS place_pct,
    ROUND(AVG(CASE WHEN finish_position<=3 THEN win_odds END)::numeric,1) AS avg_odds_placed
FROM ranked
WHERE idx_rank BETWEEN 6 AND 12
GROUP BY idx_rank
ORDER BY idx_rank
"""
    rows = await rq(session, sql, params)
    print(f"\n  【指数ランク別 3着内率（6〜12位）】")
    print(f"  {'指数位':>5} {'頭数':>6} {'3着内':>6} {'3着内率':>8} {'穴3着時の平均オッズ':>20}")
    for r in rows:
        flag = " ◀" if float(r.place_pct or 0) > 12 else ""
        avg_o = f"{r.avg_odds_placed:.1f}倍" if r.avg_odds_placed else "    N/A"
        print(f"  {r.idx_rank:>4}位 {r.cnt:>6} {r.places:>6} "
              f"{(r.place_pct or 0):>7.1f}% {avg_o:>18}{flag}")

    # 穴馬のサブ指数特徴
    sql = base_cte(mf) + """
SELECT
    COUNT(*) AS cnt,
    ROUND(AVG(speed_rank)::numeric,2) AS avg_speed,
    ROUND(AVG(last3f_rank)::numeric,2) AS avg_last3f,
    ROUND(AVG(jockey_rank)::numeric,2) AS avg_jockey,
    ROUND(AVG(pace_rank)::numeric,2) AS avg_pace,
    ROUND(AVG(course_rank)::numeric,2) AS avg_course,
    ROUND(COUNT(CASE WHEN speed_rank<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS speed_top3_pct,
    ROUND(COUNT(CASE WHEN last3f_rank<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS last3f_top3_pct,
    ROUND(COUNT(CASE WHEN jockey_rank<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS jockey_top3_pct,
    ROUND(COUNT(CASE WHEN pace_rank<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS pace_top3_pct,
    ROUND(COUNT(CASE WHEN course_rank<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS course_top3_pct
FROM ranked
WHERE idx_rank >= 6 AND finish_position <= 3
"""
    rows = await rq(session, sql, params)
    r = rows[0]
    print(f"\n  【穴馬のサブ指数特徴】（{r.cnt}頭 / 総合指数6位以下→3着内）")
    print(f"  ※ 高いTop3%＝穴馬でもそのサブ指数では上位だった傾向")
    subs = [
        ("スピード指数", float(r.avg_speed or 0), float(r.speed_top3_pct or 0)),
        ("上がり3F    ", float(r.avg_last3f or 0), float(r.last3f_top3_pct or 0)),
        ("騎手指数    ", float(r.avg_jockey or 0), float(r.jockey_top3_pct or 0)),
        ("ペース指数  ", float(r.avg_pace or 0), float(r.pace_top3_pct or 0)),
        ("コース適性  ", float(r.avg_course or 0), float(r.course_top3_pct or 0)),
    ]
    subs_sorted = sorted(subs, key=lambda x: x[2], reverse=True)
    for name, avg_rank, top3_pct in subs_sorted:
        flag = " ← 穴馬の強み" if top3_pct > 35 else ""
        print(f"    {name} : 平均{avg_rank:>5.2f}位  Top3に入っていた割合 {top3_pct:>5.1f}%{flag}")

    # 穴馬が多い競馬場Top10
    sql = base_cte(mf) + """
SELECT
    course_name,
    surface,
    COUNT(CASE WHEN idx_rank>=6 AND finish_position<=3 THEN 1 END) AS upset_cnt,
    COUNT(CASE WHEN finish_position<=3 THEN 1 END) AS total_placed,
    ROUND(
        COUNT(CASE WHEN idx_rank>=6 AND finish_position<=3 THEN 1 END)::numeric
        / NULLIF(COUNT(CASE WHEN finish_position<=3 THEN 1 END),0) * 100
    ,1) AS upset_ratio
FROM ranked
GROUP BY course_name, surface
HAVING COUNT(CASE WHEN finish_position<=3 THEN 1 END) >= 15
ORDER BY upset_ratio DESC
LIMIT 10
"""
    rows = await rq(session, sql, params)
    print(f"\n  【穴馬が多い競馬場 Top10】")
    print(f"  {'競馬場':<10} {'馬場':>4} {'穴3着内':>8} {'総3着内':>8} {'着内穴%':>8}")
    for r in rows:
        flag = " ◀" if float(r.upset_ratio or 0) > 40 else ""
        print(f"  {r.course_name:<10} {r.surface:>4} {r.upset_cnt:>8} "
              f"{r.total_placed:>8} {(r.upset_ratio or 0):>7.1f}%{flag}")


# ---------------------------------------------------------------------------
# 5. 荒れ構造分析
# ---------------------------------------------------------------------------

async def print_upset_structure(session, params: dict, mf: str) -> None:
    # 1レースの3着内に6位以下が何頭入るか
    sql = base_cte(mf) + """
, race_summary AS (
    SELECT
        race_id,
        COUNT(CASE WHEN finish_position<=3 AND idx_rank>=6 THEN 1 END) AS upsets_in_top3,
        MAX(CASE WHEN idx_rank=1 THEN finish_position END) AS rank1_pos,
        MAX(CASE WHEN idx_rank=2 THEN finish_position END) AS rank2_pos
    FROM ranked
    GROUP BY race_id
)
SELECT
    upsets_in_top3,
    COUNT(*) AS races,
    ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER () * 100, 1) AS pct,
    ROUND(AVG(rank1_pos)::numeric,2) AS avg_r1_pos,
    ROUND(AVG(rank2_pos)::numeric,2) AS avg_r2_pos
FROM race_summary
GROUP BY upsets_in_top3
ORDER BY upsets_in_top3
"""
    rows = await rq(session, sql, params)
    print(fmt("■ 荒れ構造分析"))
    print(f"\n  【3着内への穴馬混入数の分布】")
    print(f"  {'穴馬数':>6} {'レース数':>8} {'割合':>8} {'指数1位平均着順':>16} {'指数2位平均着順':>16}")
    for r in rows:
        print(f"  {r.upsets_in_top3:>5}頭 {r.races:>8} {r.pct:>7.1f}%"
              f" {r.avg_r1_pos:>15.2f}位 {(float(r.avg_r2_pos or 0)):>15.2f}位")

    # 指数1位が脱落したとき2位以下の動向
    sql = base_cte(mf) + """
, race_summary AS (
    SELECT
        race_id,
        MAX(CASE WHEN idx_rank=1 THEN finish_position END) AS rank1_pos,
        MAX(CASE WHEN idx_rank=2 THEN finish_position END) AS rank2_pos,
        MAX(CASE WHEN idx_rank=3 THEN finish_position END) AS rank3_pos
    FROM ranked
    GROUP BY race_id
)
SELECT
    CASE WHEN rank1_pos > 3 THEN '1位脱落' ELSE '1位3着内' END AS r1_status,
    COUNT(*) AS races,
    ROUND(COUNT(CASE WHEN rank2_pos<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS r2_pct,
    ROUND(COUNT(CASE WHEN rank3_pos<=3 THEN 1 END)::numeric/COUNT(*)*100,1) AS r3_pct,
    ROUND(AVG(rank2_pos)::numeric,2) AS avg_r2_pos
FROM race_summary
WHERE rank1_pos IS NOT NULL AND rank2_pos IS NOT NULL
GROUP BY r1_status
ORDER BY r1_status
"""
    rows = await rq(session, sql, params)
    print(f"\n  【指数1位脱落時の2・3位動向】")
    print(f"  {'状況':<10} {'レース数':>8} {'2位3着内率':>12} {'3位3着内率':>12} {'2位平均着順':>12}")
    for r in rows:
        print(f"  {r.r1_status:<10} {r.races:>8} {r.r2_pct:>11.1f}% "
              f"{r.r3_pct:>11.1f}% {r.avg_r2_pos:>11.2f}位")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

async def run(year: str, from_month: int, to_month: int) -> None:
    month_filter = ""
    if from_month > 1 or to_month < 12:
        month_filter = (
            f"AND CAST(LEFT(r.date, 6) AS INTEGER) BETWEEN "
            f"{year}{from_month:02d} AND {year}{to_month:02d}"
        )

    params = {"year": year}
    period = f"{year}年"
    if from_month > 1 or to_month < 12:
        period += f" {from_month}〜{to_month}月"

    print(f"\n{'='*62}")
    print(f"  指数傾向分析レポート — {period}")
    print(f"{'='*62}")

    async with AsyncSessionLocal() as session:
        await print_summary(session, params, month_filter, period)
        await print_rank_accuracy(session, params, month_filter)
        await print_miss_patterns(session, params, month_filter)
        await print_dark_horse_patterns(session, params, month_filter)
        await print_upset_structure(session, params, month_filter)

    print(f"\n{'='*62}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="指数傾向分析（上位外れ・穴馬パターン）")
    parser.add_argument("--year", required=True, help="対象年 (例: 2026)")
    parser.add_argument("--from-month", type=int, default=1, help="開始月 (1-12)")
    parser.add_argument("--to-month", type=int, default=12, help="終了月 (1-12)")
    args = parser.parse_args()
    asyncio.run(run(args.year, args.from_month, args.to_month))


if __name__ == "__main__":
    main()
