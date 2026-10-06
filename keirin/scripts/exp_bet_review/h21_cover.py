#!/usr/bin/env python3
"""H21 手順1: 7車レースごとの 三連単スナップショット一覧（種別・時刻・有効組数）と発走時刻を抽出（読み取り専用）。
出力: data/exp_bet_review/h21/cover.pkl  {races:{race_key:(date,start_epoch)}, snaps:{race_key:[(type,snapshot_at,n_rows,n_valid)]}}
"""
from __future__ import annotations
import pickle, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from _db import connect

W0, W1 = "2026-06-08", "2026-10-05"


def main():
    out = REPO / "data/exp_bet_review/h21/cover.pkl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c, c.cursor() as cur:
        cur.execute("""SELECT race_key, race_date, start_at FROM keirin.wt_races
                       WHERE n_entries=7 AND race_date BETWEEN %s AND %s AND start_at IS NOT NULL""", (W0, W1))
        races = {rk: (str(d), int(s)) for rk, d, s in cur.fetchall() if s}
        print("7車レース", len(races), flush=True)
        days = sorted({v[0] for v in races.values()})
        snaps = {}
        for d in days:
            cur.execute("""SELECT s.race_key, s.snapshot_type, s.snapshot_at, count(*),
                                  count(*) FILTER (WHERE s.odds_value > 0 AND s.odds_value < 9999)
                           FROM keirin.wt_odds_snapshot s JOIN keirin.wt_races r USING (race_key)
                           WHERE r.race_date=%s AND r.n_entries=7 AND s.bet_type='trifecta'
                           GROUP BY 1,2,3""", (d,))
            for rk, t, at, n, nv in cur.fetchall():
                snaps.setdefault(rk, []).append((t, at, n, nv))
            print(d, flush=True, end=" ")
    pickle.dump(dict(races=races, snaps=snaps), open(out, "wb"))
    print("\nsaved", out)


if __name__ == "__main__":
    main()
