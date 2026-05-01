"""v22 / v24 / v25 を 3列で比較する。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import os
import psycopg2

QUERY = """
WITH ranked AS (
  SELECT
    ci.race_id, ci.composite_index, ci.horse_id,
    rr.finish_position, rr.win_popularity, rr.win_odds,
    rr.passing_4, rr.place_odds,
    r.head_count,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank
  FROM keiba.calculated_indices ci
  JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  JOIN keiba.races r ON r.id = ci.race_id
  WHERE ci.version = %(ver)s
    AND r.head_count >= 8
    AND r.date BETWEEN %(start)s AND %(end)s
    AND COALESCE(rr.abnormality_code, 0) = 0
)
SELECT
  COUNT(*) FILTER (WHERE idx_rank = 1)                                    AS n_top1,
  ROUND(AVG(CASE WHEN idx_rank = 1 AND finish_position = 1 THEN 1.0
                 WHEN idx_rank = 1 THEN 0.0 END)::numeric * 100, 2)       AS top1_win_pct,
  ROUND(AVG(CASE WHEN idx_rank = 1 AND finish_position <= 3 THEN 1.0
                 WHEN idx_rank = 1 THEN 0.0 END)::numeric * 100, 2)       AS top1_place_pct,
  ROUND(SUM(CASE WHEN idx_rank = 1 AND finish_position = 1 THEN win_odds ELSE 0 END)
        / NULLIF(COUNT(*) FILTER (WHERE idx_rank = 1), 0), 3)             AS top1_win_roi,
  ROUND(SUM(CASE WHEN idx_rank = 1 AND finish_position <= 3
                 THEN COALESCE(place_odds, 1.5) ELSE 0 END)
        / NULLIF(COUNT(*) FILTER (WHERE idx_rank = 1), 0), 3)             AS top1_place_roi,
  ROUND(AVG(CASE WHEN idx_rank = 1 AND finish_position >= 4 AND passing_4 IS NOT NULL
                 THEN (CASE WHEN passing_4::numeric / GREATEST(head_count, 1) >= 0.45
                            THEN 1.0 ELSE 0.0 END)
                 WHEN idx_rank = 1 AND finish_position >= 4 THEN NULL END)::numeric * 100, 2)
                                                                          AS top1_miss_back_pct,
  COUNT(*) FILTER (WHERE idx_rank >= 10 AND finish_position <= 3)         AS n_low_hit,
  COUNT(*) FILTER (WHERE idx_rank = 1 AND win_popularity = 1)             AS n_pop1,
  ROUND(AVG(CASE WHEN idx_rank = 1 AND win_popularity = 1 AND finish_position <= 3
                 THEN 1.0
                 WHEN idx_rank = 1 AND win_popularity = 1 THEN 0.0 END)::numeric * 100, 2)
                                                                          AS top1_pop1_place,
  COUNT(*) FILTER (WHERE idx_rank = 1 AND win_popularity >= 6)            AS n_pop6,
  ROUND(AVG(CASE WHEN idx_rank = 1 AND win_popularity >= 6 AND finish_position <= 3
                 THEN 1.0
                 WHEN idx_rank = 1 AND win_popularity >= 6 THEN 0.0 END)::numeric * 100, 2)
                                                                          AS top1_pop6_place
FROM ranked;
"""


def fetch(cur, ver, start, end):
    cur.execute(QUERY, {"ver": ver, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone()))


def fmt(v):
    return "—" if v is None else str(v)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20260501")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    v22 = fetch(cur, 22, args.start, args.end)
    v24 = fetch(cur, 24, args.start, args.end)
    v25 = fetch(cur, 25, args.start, args.end)
    v26 = fetch(cur, 26, args.start, args.end)

    print(f"\n=== {args.start}〜{args.end} | v22 / v24 / v25 / v26 比較 ===\n")
    rows = [
        ("指数1位 サンプル数",      "n_top1"),
        ("指数1位 勝率 (%)",         "top1_win_pct"),
        ("指数1位 複勝率 (%)",       "top1_place_pct"),
        ("指数1位 単勝ROI",          "top1_win_roi"),
        ("指数1位 複勝ROI",          "top1_place_roi"),
        ("1位ハズレ→中後方率 (%)",  "top1_miss_back_pct"),
        ("下位激走 (>=10位馬券内)",  "n_low_hit"),
        ("1位×人気1位 サンプル",     "n_pop1"),
        ("1位×人気1位 複勝率 (%)",   "top1_pop1_place"),
        ("1位×人気6+ サンプル",      "n_pop6"),
        ("1位×人気6+ 複勝率 (%)",    "top1_pop6_place"),
    ]
    print(f"{'指標':<28}{'v22':<12}{'v24':<12}{'v25':<12}{'v26':<12}{'v26-v24'}")
    print("-" * 100)
    for label, key in rows:
        a, b, c, e = v22.get(key), v24.get(key), v25.get(key), v26.get(key)
        if "%" in label or "ROI" in label or "率" in label:
            try:
                d = float(e) - float(b)  # v26 vs v24
                sign = "+" if d >= 0 else ""
                diff = f"{sign}{d:.2f}"
            except (TypeError, ValueError):
                diff = ""
        else:
            diff = ""
        print(f"{label:<28}{fmt(a):<12}{fmt(b):<12}{fmt(c):<12}{fmt(e):<12}{diff}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
