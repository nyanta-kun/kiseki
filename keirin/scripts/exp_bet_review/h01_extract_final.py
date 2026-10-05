#!/usr/bin/env python3
"""H01 Step0 用: 2025 の7車台レースの確定三連単板（全210目）を DB から抽出して npz に保存（読み取り専用）。

出力: data/exp_bet_review/h01_final_tf_2025.npz
  KEY(N) / FIN(N,210)  PERMS 順の確定オッズ（無い・打ち切りは NaN） / CARS(N) 盤面に掲載の車の数
"""
from __future__ import annotations
import itertools, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from _db import connect

PERMS = list(itertools.permutations(range(1, 8), 3))
PIDX = {p: i for i, p in enumerate(PERMS)}
SENT = 9999.0   # 7車の打ち切り置き値（p1_baseline の INVALID_ODDS と同じ）


def main():
    z = np.load(REPO / "data/exp_bet_review/board_keys.npy") if False else np.load(
        REPO / "data/exp_bet_review/race_type_board.npz", allow_pickle=True)
    key, date = z["KEY"], z["DATE"]
    m = (date >= "2025-01-01") & (date <= "2025-12-31")
    keys = [str(k) for k in key[m]]
    idx = {k: i for i, k in enumerate(keys)}
    fin = np.full((len(keys), 210), np.nan)
    cars = np.zeros(len(keys), np.int8)
    with connect() as c, c.cursor() as cur:
        for i0 in range(0, len(keys), 400):
            ch = keys[i0:i0 + 400]
            cur.execute("""SELECT DISTINCT ON (race_key, combination) race_key, combination, odds_value
                           FROM keirin.wt_odds WHERE bet_type='trifecta' AND race_key = ANY(%s)
                           ORDER BY race_key, combination, collected_at DESC""", (ch,))
            seen = {}
            for rk, comb, v in cur.fetchall():
                try:
                    t = tuple(int(x) for x in str(comb).split("-"))
                except ValueError:
                    continue
                if len(t) != 3:
                    continue
                seen.setdefault(rk, set()).update(t)
                p = PIDX.get(t)
                if p is None or v is None:
                    continue
                v = float(v)
                if 0 < v < SENT:
                    fin[idx[rk], p] = v
            for rk, s in seen.items():
                cars[idx[rk]] = len(s)
            print(min(i0 + 400, len(keys)), len(keys), flush=True)
    np.savez_compressed(REPO / "data/exp_bet_review/h01_final_tf_2025.npz",
                        KEY=np.array(keys), FIN=fin.astype(np.float32), CARS=cars)
    print("saved", len(keys), "complete>=85%:", int((np.isfinite(fin).sum(1) >= 0.85 * 210).sum()),
          "cars<7:", int((cars < 7).sum()))


if __name__ == "__main__":
    main()
