#!/usr/bin/env python3
"""段0 用データ抽出（事前登録 docs/upset_index/PREREG_2026_10_02.md）。

7車・9車レースの出走（着順・vintage 指数）と三連複の最終オッズを月ごとに引き、
`data/exp_upset/s0_{entries,trio}_YYYY-MM.pkl` へ保存する（取得済みの月は飛ばす）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_upset_index/s0_extract.py 2025-01 2026-09
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2

OUT = Path(__file__).resolve().parents[2] / "data" / "exp_upset"

ENTRIES_SQL = """
SELECT r.race_key, r.race_date, r.n_entries, r.race_type, r.grade, r.venue_id,
       e.frame_no, e.player_id, e.finish_order, e.line_group, e.line_size,
       e.race_point, e.pred_win_pct, e.pred_top2_pct, e.pred_top3_pct
FROM keirin.wt_races r JOIN keirin.wt_entries e USING (race_key)
WHERE r.race_date >= %s AND r.race_date < %s AND r.n_entries IN (7, 9)
"""

TRIO_SQL = """
SELECT o.race_key, o.combination, o.odds_value
FROM keirin.wt_odds o
WHERE o.bet_type = 'trio' AND o.race_key IN (
    SELECT race_key FROM keirin.wt_races
    WHERE race_date >= %s AND race_date < %s AND n_entries IN (7, 9))
"""


def months(a: str, b: str) -> list[str]:
    """'YYYY-MM' の閉区間を列挙する。"""
    p = pd.period_range(a, b, freq="M")
    return [str(x) for x in p]


def main() -> None:
    """指定月範囲を抽出する。"""
    a, b = sys.argv[1], sys.argv[2]
    OUT.mkdir(parents=True, exist_ok=True)
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    for m in months(a, b):
        fe, ft = OUT / f"s0_entries_{m}.pkl", OUT / f"s0_trio_{m}.pkl"
        if fe.exists() and ft.exists():
            continue
        lo = f"{m}-01"
        hi = str((pd.Period(m, "M") + 1).start_time.date())
        e = pd.read_sql(ENTRIES_SQL, con, params=(lo, hi))
        t = pd.read_sql(TRIO_SQL, con, params=(lo, hi))
        e.to_pickle(fe)
        t.to_pickle(ft)
        print(m, len(e), len(t), flush=True)


if __name__ == "__main__":
    main()
