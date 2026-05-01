"""v24 + v26 アンサンブル重みの評価スクリプト（DB書き込みなし）。

現在 DB に入っている v26 は LGB-only スケール (15-85) と仮定し、
各重みペアで合成 composite_index をその場で計算→レース内 1位馬の指標を集計。

Usage:
    .venv/bin/python scripts/eval_v26_ensemble_weights.py \
        --start 20260101 --end 20260501 \
        --weights 1.0:0.0 0.7:0.3 0.5:0.5 0.3:0.7
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2

QUERY_TMPL = """
WITH joined AS (
  SELECT
    ci26.race_id,
    ci26.horse_id,
    ci26.composite_index AS lgb_score,
    ci24.composite_index AS v24_score,
    ({w_lgb} * ci26.composite_index + {w_lin} * ci24.composite_index) AS combined,
    rr.finish_position, rr.win_popularity, rr.win_odds,
    rr.passing_4, rr.place_odds,
    r.head_count
  FROM keiba.calculated_indices ci26
  JOIN keiba.calculated_indices ci24
    ON ci24.race_id = ci26.race_id
   AND ci24.horse_id = ci26.horse_id
   AND ci24.version = 24
  JOIN keiba.race_results rr
    ON rr.race_id = ci26.race_id AND rr.horse_id = ci26.horse_id
  JOIN keiba.races r ON r.id = ci26.race_id
  WHERE ci26.version = 26
    AND r.head_count >= 8
    AND r.date BETWEEN %(start)s AND %(end)s
    AND COALESCE(rr.abnormality_code, 0) = 0
), ranked AS (
  SELECT
    *,
    RANK() OVER (PARTITION BY race_id ORDER BY combined DESC) AS idx_rank
  FROM joined
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


def evaluate(cur, w_lgb: float, w_lin: float, start: str, end: str) -> dict:
    sql = QUERY_TMPL.format(w_lgb=w_lgb, w_lin=w_lin)
    cur.execute(sql, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone()))


def fmt(v):
    return "—" if v is None else str(v)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20260101")
    p.add_argument("--end", default="20260501")
    p.add_argument(
        "--weights",
        nargs="+",
        default=["1.0:0.0", "0.7:0.3", "0.5:0.5", "0.3:0.7"],
        help="LGB:LIN ペアのリスト",
    )
    args = p.parse_args()

    pairs = [tuple(map(float, w.split(":"))) for w in args.weights]

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    results: dict[str, dict] = {}
    for w_lgb, w_lin in pairs:
        label = f"{w_lgb:.1f}/{w_lin:.1f}"
        print(f"  evaluating {label} ...", file=sys.stderr)
        results[label] = evaluate(cur, w_lgb, w_lin, args.start, args.end)

    print(f"\n=== {args.start}〜{args.end} | アンサンブル重み比較 (LGB/Linear) ===\n")

    rows = [
        ("指数1位 サンプル数",     "n_top1"),
        ("指数1位 勝率 (%)",       "top1_win_pct"),
        ("指数1位 複勝率 (%)",     "top1_place_pct"),
        ("指数1位 単勝ROI",        "top1_win_roi"),
        ("指数1位 複勝ROI",        "top1_place_roi"),
        ("1位ハズレ→中後方率 (%)", "top1_miss_back_pct"),
        ("1位×人気1位 サンプル",   "n_pop1"),
        ("1位×人気1位 複勝率 (%)", "top1_pop1_place"),
        ("1位×人気6+ サンプル",    "n_pop6"),
        ("1位×人気6+ 複勝率 (%)",  "top1_pop6_place"),
    ]
    header_cols = list(results.keys())
    print(f"{'指標':<28}" + "".join(f"{c:<14}" for c in header_cols))
    print("-" * (28 + 14 * len(header_cols)))
    for label, key in rows:
        line = f"{label:<28}"
        for col in header_cols:
            line += f"{fmt(results[col].get(key)):<14}"
        print(line)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
