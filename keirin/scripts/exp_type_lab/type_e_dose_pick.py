#!/usr/bin/env python3
"""型E: 三連複へ振る「量」は決まった。では**どの33%を譲るか**（2026-09-03）。

`type_e_order_split.py`: 順序の読めなさも rp_sd も無作為対照に両窓で勝てない＝**信号は無い**。
`type_e_dose.py`: 効くのは量だけ。33% 振替で表示的中 +2.99/+1.99pt（両窓でCIが0を跨がない）・
                  ROI 不変・10万+ ほぼ据え置き・**代償は払戻中央 −12〜13%**。

信号が無いなら、選ぶ基準は「当てやすさ」ではなく **代償が小さい側**にすべき。
＝ 三連複でも払戻が残るレースから先に譲る。

腕（すべて 33% 振替・同じ量）:
  ord_w8昇順   … 順序が読めない側から（信号のつもりだった順）
  払戻降順     … 三連複4点の想定平均払戻が**大きい**側から譲る ← 代償最小化
  払戻昇順     … その逆（悪化するはずの対照）
  無作為       … 20seed の中央
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, random

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import ctx
from src.type_lab import PLANS, Plan
from type_e_order_split import run, conds

TRIO4 = Plan("x", "E", "trio", "axis2_flow", 4, alloc="dutch")
E_HIT = PLANS["E_hit"]
DOSE = 0.33


def paired(a, b, nb=3000, seed=11):
    rng = np.random.default_rng(seed); n = len(a)
    ds, dr = [], []
    for _ in range(nb):
        j = rng.integers(0, n, n)
        Sa = C.summarize([a[k] for k in j], 1); Sb = C.summarize([b[k] for k in j], 1)
        ds.append(Sb["shown"] - Sa["shown"]); dr.append(Sb["roi"] - Sa["roi"])
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
            t3 = run(x, TRIO4)
            rows.append(dict(tf=tf, t3=t3, w=conds(x)["ord_w8 上位8集合の加重平均"],
                             m3=(t3 or {}).get("mean", 0.0)))
        base = [r["tf"] for r in rows]
        cand = [j for j, r in enumerate(rows) if r["t3"]]
        n_sw = int(round(len(rows) * DOSE))
        n_sw = min(n_sw, len(cand))
        print("\n" + "=" * 112)
        print(f"███ 型E {label}  n={len(rows):,}R / {nd}日  33%振替 = {n_sw}R"
              f"（三連複が組めるのは {len(cand)}R）")
        print(f"  {'譲る順':16s} {'点数':>5s} {'表示的中%':>9s} {'Δ表示的中 CI':>19s}"
              f" {'払戻中央':>9s} {'10万+/日':>8s} {'2倍+/日':>8s} {'ROI%':>7s} {'ΔROI CI':>17s}")

        def show(name, pick):
            arm = [(rows[j]["t3"] if j in pick else rows[j]["tf"]) for j in range(len(rows))]
            s = C.summarize(arm, nd)
            cis, cir = paired(base, arm)
            print(f"  {name:16s} {s['k']:5.2f} {s['shown']:9.2f}"
                  f" {f'[{cis[0]:+.2f},{cis[1]:+.2f}]':>19s} {s['med_pay']:9,.0f}"
                  f" {s['big_per_day']:8.3f} {s['two_per_day']:8.2f} {s['roi']:7.1f}"
                  f" {f'[{cir[0]:+.1f},{cir[1]:+.1f}]':>17s}")
            return s

        b = C.summarize(base, nd)
        print(f"  {'現行(0%)':16s} {b['k']:5.2f} {b['shown']:9.2f} {'—':>19s}"
              f" {b['med_pay']:9,.0f} {b['big_per_day']:8.3f} {b['two_per_day']:8.2f}"
              f" {b['roi']:7.1f} {'—':>17s}")
        show("ord_w8 昇順", set(sorted(cand, key=lambda j: rows[j]["w"])[:n_sw]))
        show("三連複払戻 降順", set(sorted(cand, key=lambda j: -rows[j]["m3"])[:n_sw]))
        show("三連複払戻 昇順", set(sorted(cand, key=lambda j: rows[j]["m3"])[:n_sw]))
        ms = []
        for seed in range(20):
            rng = random.Random(seed * 977 + 13)
            pick = set(rng.sample(cand, n_sw))
            arm = [(rows[j]["t3"] if j in pick else rows[j]["tf"]) for j in range(len(rows))]
            ms.append(C.summarize(arm, nd))
        med = lambda k: np.median([m[k] for m in ms])
        print(f"  {'無作為20本 中央':16s} {med('k'):5.2f} {med('shown'):9.2f} {'—':>19s}"
              f" {med('med_pay'):9,.0f} {med('big_per_day'):8.3f} {med('two_per_day'):8.2f}"
              f" {med('roi'):7.1f} {'—':>17s}")


if __name__ == "__main__":
    main()
