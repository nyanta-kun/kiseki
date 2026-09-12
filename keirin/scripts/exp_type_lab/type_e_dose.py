#!/usr/bin/env python3
"""型E: 三連複への振替は「条件」ではなく「量」か（2026-09-03）。

`type_e_order_split.py` で、順序の読めなさ（ord_conc/ord_top2/ord_w8）も rp_sd も
**無作為対照に両窓で勝てない**（3/20〜17/20・窓で反転）と出た。
残る問いは「量（何割を三連複へ振るか）」の応答曲線と、その CI。

腕: gate を通る三連複のうち下位 d%（ord_w8 昇順＝実質どれでも同じ）を三連複4点へ。
"""
from __future__ import annotations
import sys, itertools
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import ctx
from src.type_lab import PLANS, Plan
from type_e_order_split import run, conds

TRIO4 = Plan("x", "E", "trio", "axis2_flow", 4, alloc="dutch")
E_HIT = PLANS["E_hit"]
DOSES = (0.0, 0.10, 0.20, 0.33, 0.50, 1.00)


def paired(a, b, nb=3000, seed=11):
    rng = np.random.default_rng(seed)
    n = len(a)
    ds, dr, dp = [], [], []
    for _ in range(nb):
        j = rng.integers(0, n, n)
        sa = [a[k] for k in j]; sb = [b[k] for k in j]
        Sa, Sb = C.summarize(sa, 1), C.summarize(sb, 1)
        ds.append(Sb["shown"] - Sa["shown"]); dr.append(Sb["roi"] - Sa["roi"])
        dp.append(Sb["big_per_day"] - Sa["big_per_day"])
    q = lambda v: (np.percentile(v, 2.5), np.percentile(v, 97.5))
    return q(ds), q(dr)


def main():
    for label, win in (("探索 2024-07〜2025-12 (in-sample)", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        nd = C.days_of(C.select(None, win))
        rows = []
        for i in [int(v) for v in C.select("E", win)]:
            x = ctx(i)
            if x is None:
                continue
            tf = run(x, E_HIT)
            if not tf:
                continue
            rows.append(dict(tf=tf, t3=run(x, TRIO4), w=conds(x)["ord_w8 上位8集合の加重平均"]))
        v = np.array([r["w"] for r in rows])
        base = [r["tf"] for r in rows]
        print("\n" + "=" * 116)
        print(f"███ 型E {label}  n={len(rows):,}R / {nd}日   "
              f"三連複がゲートを通る割合 {sum(1 for r in rows if r['t3'])/len(rows)*100:.1f}%")
        print(f"  {'振替量':14s} {'実振替R':>7s} {'点数':>5s} {'表示的中%':>9s} {'Δ表示的中 CI':>20s}"
              f" {'払戻中央':>9s} {'10万+/日':>8s} {'ROI%':>7s} {'ΔROI CI':>18s}")
        for d in DOSES:
            if d == 0:
                arm = base
            else:
                thr = float(np.quantile(v, d)) if d < 1 else float("inf")
                arm = [(r["t3"] or r["tf"]) if r["w"] <= thr else r["tf"] for r in rows]
            nsw = sum(1 for x0, x1 in zip(base, arm) if x0 is not x1)
            s = C.summarize(arm, nd)
            if d == 0:
                cis, cir = (0, 0), (0, 0)
            else:
                cis, cir = paired(base, arm)
            print(f"  {f'{int(d*100)}%':14s} {nsw:7d} {s['k']:5.2f} {s['shown']:9.2f}"
                  f" {f'[{cis[0]:+.2f},{cis[1]:+.2f}]':>20s} {s['med_pay']:9,.0f}"
                  f" {s['big_per_day']:8.3f} {s['roi']:7.1f}"
                  f" {f'[{cir[0]:+.1f},{cir[1]:+.1f}]':>18s}")


if __name__ == "__main__":
    main()
