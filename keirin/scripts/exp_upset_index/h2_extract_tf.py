#!/usr/bin/env python3
"""H2 用: 7車・9車レースの三連単最終オッズを月ごとに `data/exp_upset/h2_tf_YYYY-MM.pkl` へ保存する。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_upset_index/h2_extract_tf.py 2025-01 2026-09
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import psycopg2

from scripts.exp_upset_index.s0_extract import OUT, months

TF_SQL = """
SELECT o.race_key, o.combination, o.odds_value
FROM keirin.wt_odds o
WHERE o.bet_type = 'trifecta' AND o.race_key IN (
    SELECT race_key FROM keirin.wt_races
    WHERE race_date >= %s AND race_date < %s AND n_entries IN (7, 9))
"""


def main() -> None:
    """指定月範囲を抽出する。"""
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    for m in months(sys.argv[1], sys.argv[2]):
        f = OUT / f"h2_tf_{m}.pkl"
        if f.exists():
            continue
        lo = f"{m}-01"
        hi = str((pd.Period(m, "M") + 1).start_time.date())
        t = pd.read_sql(TF_SQL, con, params=(lo, hi))
        t["odds_value"] = t.odds_value.astype("float32")
        t.to_pickle(f)
        print(m, len(t), flush=True)


if __name__ == "__main__":
    main()
