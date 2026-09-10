#!/usr/bin/env python3
"""券種検証の台を作る（2026-09-10・ユーザー依頼）。

板 `/tmp/race_type_board.npz`（7車 36,427R）へ **2車券の確定オッズ**を足す。
    WIDE (N,21)  quinellaPlace   QN (N,21)  quinella   EX (N,42)  exacta

🔴 `wt_odds.combination` の区切りは **`=` と `-` が混在**する（2026-06 以降の一部が `-`）。
   `=` だけで引くと 2026年の 21.4% が静かに欠測する（`type_e_2026_09_01.md` 11-2）。
   ここは `[-=→]` で割る。
🔴 `wt_odds` は**確定オッズ**（`build_race_type_board.py` が三連複の確定オッズとして
   同じテーブルを使っている）。予測ではない。

出力: /tmp/bet_type_2car.npz
"""
from __future__ import annotations

import itertools
import os
import re
import sys
from pathlib import Path

import numpy as np
import psycopg2

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PAIRS = list(itertools.combinations(range(1, 8), 2))          # 21
PIDX = {frozenset(p): i for i, p in enumerate(PAIRS)}
ORD = [p for p in itertools.permutations(range(1, 8), 2)]     # 42
OIDX = {p: i for i, p in enumerate(ORD)}


def main() -> None:
    z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
    KEY = [str(k) for k in z["KEY"]]
    N = len(KEY)
    idx = {k: i for i, k in enumerate(KEY)}
    WIDE = np.full((N, 21), np.nan, np.float32)
    QN = np.full((N, 21), np.nan, np.float32)
    EX = np.full((N, 42), np.nan, np.float32)
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    for i0 in range(0, N, 2000):
        ch = KEY[i0:i0 + 2000]
        cur.execute("""SELECT race_key, bet_type, combination, odds_value
                       FROM keirin.wt_odds
                       WHERE bet_type IN ('quinellaPlace','quinella','exacta')
                         AND race_key = ANY(%s)""", (ch,))
        for rk, bt, comb, od in cur.fetchall():
            try:
                v = float(od)
            except (TypeError, ValueError):
                continue
            if not (0 < v < 99999):
                continue
            cars = [int(x) for x in re.split(r"[-=→]", str(comb)) if x.strip().isdigit()]
            if len(cars) != 2 or any(c < 1 or c > 7 for c in cars):
                continue
            r = idx[rk]
            if bt == "exacta":
                j = OIDX.get((cars[0], cars[1]))
                if j is not None:
                    EX[r, j] = v
            else:
                j = PIDX.get(frozenset(cars))
                if j is None:
                    continue
                (WIDE if bt == "quinellaPlace" else QN)[r, j] = v
        print(f"  {min(i0+2000, N):,}/{N:,}", flush=True)
    con.close()
    np.savez_compressed("/tmp/bet_type_2car.npz", WIDE=WIDE, QN=QN, EX=EX)
    for nm, a in (("wide", WIDE), ("quinella", QN), ("exacta", EX)):
        full = np.isfinite(a).all(axis=1).mean() * 100
        print(f"{nm}: 全点そろっているレース {full:.2f}%")


if __name__ == "__main__":
    main()
