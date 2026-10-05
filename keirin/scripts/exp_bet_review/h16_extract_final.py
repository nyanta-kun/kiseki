#!/usr/bin/env python3
"""H16 Step0 用: 2025 の7車台レースの確定三連複板（全35目）を DB から抽出（読み取り専用・月単位）。

出力: data/exp_bet_review/h16_final_trio_2025.npz  KEY(N) / FIN3(N,35)  C3 順の確定オッズ（無い・打ち切りは NaN）
"""
from __future__ import annotations
import itertools, re, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from _db import connect

C3 = list(itertools.combinations(range(1, 8), 3))
CIDX = {frozenset(c): i for i, c in enumerate(C3)}
SENT = 9999.0


def main():
    z = np.load(REPO / "data/exp_bet_review/race_type_board.npz", allow_pickle=True)
    key, date = z["KEY"], z["DATE"]
    m = (date >= "2025-01-01") & (date <= "2025-12-31")
    keys = [str(k) for k in key[m]]
    idx = {k: i for i, k in enumerate(keys)}
    fin = np.full((len(keys), 35), np.nan)
    with connect() as c, c.cursor() as cur:
        for i0 in range(0, len(keys), 400):
            ch = keys[i0:i0 + 400]
            cur.execute("""SELECT DISTINCT ON (race_key, combination) race_key, combination, odds_value
                           FROM keirin.wt_odds WHERE bet_type='trio' AND race_key = ANY(%s)
                           ORDER BY race_key, combination, collected_at DESC""", (ch,))
            for rk, comb, v in cur.fetchall():
                try:
                    t = frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))
                except ValueError:
                    continue
                j = CIDX.get(t)
                if j is None or v is None or len(t) != 3:
                    continue
                v = float(v)
                if 0 < v < SENT:
                    fin[idx[rk], j] = v
            print(min(i0 + 400, len(keys)), len(keys), flush=True)
    np.savez_compressed(REPO / "data/exp_bet_review/h16_final_trio_2025.npz",
                        KEY=np.array(keys), FIN3=fin.astype(np.float32))
    print("saved", len(keys), "complete:", int((np.isfinite(fin).sum(1) == 35).sum()))


if __name__ == "__main__":
    main()
