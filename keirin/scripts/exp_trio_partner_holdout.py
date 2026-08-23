#!/usr/bin/env python3
"""相手順位の優位を **2024-01〜2025-12 の完全独立窓**で確認する。

## 位置づけ

`exp_trio_partner_rank.py` は 2026-01〜08（14,941R）で
「軸2車＋相手＝順位5」が両窓で壁を超えることを示した。だが

- 順位5 は **5つの順位から最良を選んだ**もの（多重比較）
- 探索/確認の分割は同じ8ヶ月の中での前後分割にすぎない

🔴 **順位5の規則は `p3` の順位しか使わない**ので、三連単オッズモデルの
   学習終端（2025-12-31）に縛られない。**2024-01〜2025-12 は今回の探索に
   一度も使っていない完全な独立窓**（約3倍の規模）。ここで再現するかを見る。

判定は事前登録: **日ブロック bootstrap の CI 下限 > 払戻率 74.85%**、
かつ **順位5 が他の順位より上**であること。年別・月別の一貫性も出す。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402

PAYOUT_RATE = 0.7485


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/trio_rank_cache.jsonl")
    args = ap.parse_args()
    np.random.seed(281)

    rows = [json.loads(x) for x in open(args.cache)]
    keys = [r["race_key"] for r in rows]
    print(f"独立窓 {len(rows):,}R "
          f"（{min(r['race_date'] for r in rows)}〜{max(r['race_date'] for r in rows)}）")

    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
    board: dict[str, dict] = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                    "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                    (keys[i:i + 2000],))
        for rk, c, o in cur.fetchall():
            s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(s) == 3:
                board[rk][s] = float(o)
    fins = _load_finishes(keys)
    print(f"板あり {len(board):,}R / 着順あり {len(fins):,}R\n")

    rec = defaultdict(list)          # 相手順位 -> [(date, bet, pay)]
    n_used = 0
    both = 0
    for r in rows:
        bd = board.get(r["race_key"]); order3 = fins.get(r["race_key"])
        if not bd or not order3:
            continue
        wins = {frozenset(w) for w in winning_trifectas(order3)}
        o = r["order"]
        if len(o) < 7:
            continue
        n_used += 1
        top3 = {c for w in wins for c in w}
        if o[0] in top3 and o[1] in top3:
            both += 1
        for i in range(2, 7):
            k = frozenset((o[0], o[1], o[i]))
            if k not in bd:
                continue
            rec[i + 1].append((r["race_date"], 10000,
                               int(bd[k] * 100) * 10000 // 100 if k in wins else 0))
    print(f"採点できた {n_used:,}R / 二軸(順位1-2)がともに3着内 {both/n_used:.2%}\n")

    def roi_ci(seg, B=2000):
        by = defaultdict(lambda: [0.0, 0.0])
        for d, b, p in seg:
            a = by[d]; a[0] += b; a[1] += p
        v = list(by.values())
        bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
        idx = np.random.randint(0, len(v), size=(B, len(v)))
        bs = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
        return pay.sum() / bet.sum(), bs[int(B * .025)], bs[int(B * .975)]

    print(f"{'相手':>6}{'R数':>9}{'的中率':>8}{'ROI':>8}{'CI95':>18}{'中央払戻':>10}")
    for i in range(3, 8):
        seg = rec[i]
        r, lo, hi = roi_ci(seg)
        pl = sorted(p for _, _, p in seg if p > 0)
        mk = " 🟢壁超" if lo > PAYOUT_RATE else ""
        print(f"{f'順位{i}':>6}{len(seg):>9,}"
              f"{sum(1 for x in seg if x[2] > 0)/len(seg):>8.2%}{r:>8.1%}"
              f"  [{lo:>5.1%},{hi:>6.1%}]{(np.median(pl) if pl else 0):>10,.0f}{mk}")

    print(f"\n--- 年別・月別の一貫性（1点・順位別 ROI）---")
    by = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for i in range(3, 8):
        for d, b, p in rec[i]:
            a = by[d[:7]][i]; a[0] += b; a[1] += p
    print(f"{'月':10}" + "".join(f"{f'順位{r}':>9}" for r in range(3, 8)) + "  最良")
    n_best = 0; n_wall = 0; n_mon = 0
    for m in sorted(by):
        vals = {i: (by[m][i][1] / by[m][i][0] if by[m][i][0] else 0) for i in range(3, 8)}
        best = max(vals, key=vals.get)
        n_mon += 1
        n_best += (best == 5)
        n_wall += (vals[5] > PAYOUT_RATE)
        print(f"{m:10}" + "".join(f"{vals[i]:>9.1%}" for i in range(3, 8))
              + f"  順位{best}")
    print(f"\n順位5が最良だった月: {n_best}/{n_mon}"
          f"  ／ 順位5が壁を超えた月: {n_wall}/{n_mon}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
