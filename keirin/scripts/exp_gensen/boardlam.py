#!/usr/bin/env python3
"""210点の買い目確率そのものに同ライン隣接ボーナスを入れる（1-2着 λ / 2-3着 μ）。
top1/top5 の的中率と払戻を探索(2024H2-2025)・確認(2026)で見る。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3))
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, WIN, PAY, OK, DATE, LG, LPOS = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"], z["LG"], z["A_line_pos"]
ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
A12 = np.zeros((len(ii), 210), bool); A23 = np.zeros((len(ii), 210), bool)
for r, i in enumerate(ii):
    def adj(a, b):
        return (LG[i][a-1] == LG[i][b-1] and LG[i][a-1] not in ("", "0")
                and LPOS[i][b-1] == LPOS[i][a-1] + 1)
    for t, (a, b, c) in enumerate(CANON):
        A12[r, t] = adj(a, b); A23[r, t] = adj(b, c)
P = PROB[ii]; W = WIN[ii]; PY_ = PAY[ii]; D = np.array([str(DATE[i]) for i in ii])
EX = (D >= "2024-07-01") & (D <= "2025-12-31"); CO = (D >= "2026-01-01")

def ev(lam, mu, k, m):
    s = P * np.where(A12, lam, 1.0) * np.where(A23, mu, 1.0)
    top = np.argsort(-s, 1)[:, :k]
    hit = (top == W[:, None]).any(1)
    st = BUDGET // k // UNIT * UNIT
    pay = np.where(hit, PY_ * st / 100.0, 0.0)
    nd = len(set(D[m]))
    hs = sorted(pay[m][pay[m] > 0])
    return dict(hit=hit[m].mean()*100, roi=pay[m].sum()/(m.sum()*st*k)*100,
                med=median(hs) if hs else 0, b10=sum(1 for p in hs if p >= 100_000)/nd)

print(" λ(1-2)  μ(2-3)  k | 探索: 的中   ROI   | 確認: 的中   ROI    中央    10万+/日")
for k in (1, 5):
    for lam, mu in [(1,1),(1.5,1),(2,1),(2.5,1),(3,1),(2.5,1.2),(2.5,1.5),(1,1.5),(2,1.5),(2,2)]:
        a = ev(lam, mu, k, EX); b = ev(lam, mu, k, CO)
        print(f"  {lam:4.1f}   {mu:4.1f}   {k} | {a['hit']:6.2f}% {a['roi']:6.1f}% | "
              f"{b['hit']:6.2f}% {b['roi']:6.1f}% {b['med']:>9,.0f} {b['b10']:7.3f}")
    print()
