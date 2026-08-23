#!/usr/bin/env python3
"""1日の件数上限を外し、**厳選率だけ**を振ったらどうなるか（ユーザー指示 2026-08-23）。

> 今回の検証においては日に20件の制約を持たず、確実に買えるレースを厳選すると
> どの様な結果が得られるか

🔴 **上限があると「厳選」の効果と「上限による選択」が混ざる。**
   実際、上限20件のもとでは「中は買わない」が ROI +8.8pt に見えたが、
   上限を外すと 83.6%（全レース1点）と区別できなかった。ここでは上限を置かない。

## 並べ方（すべて朝8:00 に手に入る）

| 記号 | 指標 | 意味 |
|---|---|---|
| `conf` | `z(axis_sum) − z(bad2)` | 二軸が成立しそうか（二軸的中 39.9%↔70.3% を分離） |
| `phit` | 買った目の PL 確率の和 | 買い目が当たりそうか（直接的） |
| `odds` | 買った目の予測オッズ（低い順） | 市場から見て堅いか |

🔴 **厳選しても ROI は上がらない**というのが事前の予想（レース形状シグナルは
   市場が織り込む）。ここで見たいのは **どこまで的中率を上げられるか / そのとき
   払戻と件数がどうなるか**。ROI が上がったらむしろ多重比較を疑うこと。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_leg_prob_heads import strengths  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485


def trio_prob(pw, p3, cars3) -> float:
    """三連複の的中確率（位置別合成 PL を6順列ぶん合算）。"""
    s = [strengths(pw, p3, a) for a in (1.0, 0.5, 0.0)]
    s1 = sum(s[0].values())
    cars = list(pw)
    tot = 0.0
    for x, y, z in itertools.permutations(cars, 3):
        d2 = sum(s[1][c] for c in cars if c != x)
        d3 = sum(s[2][c] for c in cars if c not in (x, y))
        if d2 <= 0 or d3 <= 0:
            continue
        v = (s[0][x] / s1) * (s[1][y] / d2) * (s[2][z] / d3)
        if frozenset((x, y, z)) == cars3:
            tot += v
    return tot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    args = ap.parse_args()
    np.random.seed(241)

    rows = []
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win") and r.get("bad"):
                rows.append(r)
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
    keys = [r["race_key"] for r in rows]; board = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                    "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                    (keys[i:i + 2000],))
        for rk, c, o in cur.fetchall():
            s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(s) == 3:
                board[rk][s] = float(o)

    R = []
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        pw = {int(k): v for k, v in r["pw"].items()}
        bad = {int(k): v for k, v in r["bad"].items()}
        if min(len(p3), len(pw), len(bad)) < 7:
            continue
        o = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        a1, a2 = o[0], o[1]
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        shapes = {}
        for name, idx in (("1点(順位5)", [4]), ("2点(5,3)", [4, 2]),
                          ("3点(5,4,3)", [4, 3, 2]), ("5点(総流し)", [2, 3, 4, 5, 6])):
            legs = [frozenset((a1, a2, o[i])) for i in idx]
            legs = [k for k in legs if k in bd]
            if not legs:
                continue
            stake = unit_stake(len(legs))
            shapes[name] = dict(
                bet=stake * len(legs),
                pay=next((int(bd[k] * 100) * stake // 100 for k in legs if k in wins), 0),
                phit=sum(trio_prob(pw, p3, k) for k in legs),
                odds=st.mean([bd[k] for k in legs]))
        if not shapes:
            continue
        R.append(dict(date=r["race_date"], conf=None,
                      axis_sum=p3[a1] + p3[a2], bad2=bad[a2], shapes=shapes))

    sel = [x for x in R if x["date"] < args.split]
    ma, sa = st.mean([x["axis_sum"] for x in sel]), st.pstdev([x["axis_sum"] for x in sel])
    mb, sb = st.mean([x["bad2"] for x in sel]), st.pstdev([x["bad2"] for x in sel])
    for x in R:
        x["conf"] = (x["axis_sum"] - ma) / (sa or 1) - (x["bad2"] - mb) / (sb or 1)
    n_days = len({x["date"] for x in R})
    print(f"{len(R):,}R / {n_days}日（上限なし）  壁 {PAYOUT_RATE:.2%}\n")

    def roi_ci(seg, B=2000):
        by = defaultdict(lambda: [0.0, 0.0])
        for x in seg:
            a = by[x[0]]; a[0] += x[1]; a[1] += x[2]
        v = list(by.values())
        bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
        idx = np.random.randint(0, len(v), size=(B, len(v)))
        b = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
        return pay.sum() / bet.sum(), b[int(B * .025)], len(v)

    for shape in ("1点(順位5)", "3点(5,4,3)", "5点(総流し)"):
        print(f"===== 買い目 {shape} =====")
        print(f"{'並べ方':8}{'厳選率':>7}{'窓':>5}{'R数':>8}{'件/日':>6}{'的中%':>8}"
              f"{'ROI':>8}{'CI下限':>8}{'中央払戻':>10}")
        for order, key, rev in (("conf", lambda x: x["conf"], True),
                                ("phit", lambda x: x["shapes"][shape]["phit"], True),
                                ("odds低", lambda x: x["shapes"][shape]["odds"], False)):
            avail = [x for x in R if shape in x["shapes"]]
            s_ = sorted([x for x in avail if x["date"] < args.split],
                        key=key, reverse=rev)
            c_ = sorted([x for x in avail if x["date"] >= args.split],
                        key=key, reverse=rev)
            for frac in (0.01, 0.05, 0.10, 0.25, 0.50, 1.00):
                for wn, src in (("探索", s_), ("確認", c_)):
                    take = src[:max(1, int(len(src) * frac))]
                    seg = [(x["date"], x["shapes"][shape]["bet"],
                            x["shapes"][shape]["pay"]) for x in take]
                    if len(seg) < 60:
                        continue
                    r, lo, nd = roi_ci(seg)
                    pl = sorted(z[2] for z in seg if z[2] > 0)
                    mk = " 🟢" if lo > PAYOUT_RATE else ""
                    lbl = order if (frac == 0.01 and wn == "探索") else ""
                    fr = f"{frac:.0%}" if wn == "探索" else ""
                    print(f"{lbl:8}{fr:>7}{wn:>5}{len(seg):>8,}"
                          f"{len(seg)/nd:>6.1f}"
                          f"{sum(1 for z in seg if z[2]>0)/len(seg):>8.2%}{r:>8.1%}"
                          f"{lo:>8.1%}{(st.median(pl) if pl else 0):>10,.0f}{mk}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
