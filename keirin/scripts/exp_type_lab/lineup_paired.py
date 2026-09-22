#!/usr/bin/env python3
"""2腕の対比較（日ブートストラップ）。同じ日・同じ台で組み直しているので対応あり。"""
from __future__ import annotations
import argparse, random, statistics, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lineup_sim import board, ctx
import lineup_arms as R
import src.type_lab as TL


def by_day(recs):
    g = defaultdict(list)
    for r in recs:
        g[r["day"]].append(r)
    return g


def stats(days, g):
    n = inv = pay = hit = b3 = b4 = b10 = 0
    for d in days:
        for r in g.get(d, ()):
            n += 1; inv += r["inv"]; pay += r["pay"]
            if r["pay"] > r["inv"]:
                hit += 1
                b3 += r["pay"] >= 30_000; b4 += r["pay"] >= 40_000; b10 += r["pay"] >= 100_000
    nd = len(days)
    return dict(shown=hit/n*100 if n else 0, roi=pay/inv*100 if inv else 0,
                b3=b3/nd, b4=b4/nd, b10=b10/nd, perday=n/nd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--a", default="current"); ap.add_argument("--b", required=True)
    ap.add_argument("--n", type=int, default=2000)
    args = ap.parse_args()
    z = board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"])
         & z["OKPRED"] & (z["DATE"] >= args.start) & (z["DATE"] <= args.end))
    idx = np.flatnonzero(m)
    cache = {int(i): ctx(int(i)) for i in idx}
    ok = [int(i) for i in idx if cache[int(i)] is not None]
    big0 = TL.HIGHPAY_BIG_SLOTS
    addperm0, gf0 = TL.ADD_PERM_PLANS, dict(TL.GATE_FALLBACK)

    def build_arm(a):
        spec = a.split("+"); base = spec[0]
        TL.HIGHPAY_BIG_SLOTS = frozenset() if "h1" in spec[1:] else big0
        TL.ADD_PERM_PLANS = frozenset() if base == "v0907" else addperm0
        TL.GATE_FALLBACK = ({k: v for k, v in gf0.items() if k != "F_hit"}
                            if base == "v0907" else gf0)
        if base == "v0907":
            plans = R.v0907()
        elif base == "current":
            plans = {}
        elif base.startswith("dutch"):
            p = base.split(":")
            plans = R.dutch_target(int(p[1]), p[2] if len(p) > 2 else "ABCDEF",
                                   float(p[3]) if len(p) > 3 else 600.0)
        else:
            plans = R.core_floor(int(base))
        out = R.run(a, plans, ok, cache)
        TL.HIGHPAY_BIG_SLOTS, TL.ADD_PERM_PLANS = big0, addperm0
        TL.GATE_FALLBACK = gf0
        return by_day(out)

    ga, gb = build_arm(args.a), build_arm(args.b)
    days = sorted(set(ga) | set(gb))
    sa, sb = stats(days, ga), stats(days, gb)
    print(f"{'指標':<10}{args.a:>14}{args.b:>16}{'Δ':>9}   95%CI")
    boots = {k: [] for k in ("shown", "roi", "b3", "b4", "b10", "perday")}
    rnd = random.Random(7)
    for _ in range(args.n):
        smp = [rnd.choice(days) for _ in days]
        x, y = stats(smp, ga), stats(smp, gb)
        for k in boots:
            boots[k].append(y[k] - x[k])
    for k, lab in (("perday","件/日"),("shown","表示的中%"),("roi","ROI%"),
                   ("b3","3万+/日"),("b4","4万+/日"),("b10","10万+/日")):
        v = sorted(boots[k]); lo, hi = v[int(.025*len(v))], v[int(.975*len(v))]
        print(f"{lab:<10}{sa[k]:>14.2f}{sb[k]:>16.2f}{sb[k]-sa[k]:>+9.2f}   [{lo:+.2f}, {hi:+.2f}]")


if __name__ == "__main__":
    main()
