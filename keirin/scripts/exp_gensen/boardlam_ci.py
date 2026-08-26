#!/usr/bin/env python3
"""同ライン隣接ボーナス(λ=2.0, μ=1.5) の効果を四半期別＋日次ブートストラップで確認。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, WIN, PAY, OK, DATE, LG, LPOS = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"], z["LG"], z["A_line_pos"]
ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
A12 = np.zeros((len(ii), 210), bool); A23 = np.zeros((len(ii), 210), bool)
for r, i in enumerate(ii):
    def adj(a, b):
        return (LG[i][a-1] == LG[i][b-1] and LG[i][a-1] not in ("", "0") and LPOS[i][b-1] == LPOS[i][a-1] + 1)
    for t, (a, b, c) in enumerate(CANON):
        A12[r, t] = adj(a, b); A23[r, t] = adj(b, c)
P = PROB[ii]; W = WIN[ii]; PY_ = PAY[ii]; D = np.array([str(DATE[i]) for i in ii])
LAM, MU, K = 2.0, 1.5, 1
def arm(lam, mu):
    s = P * np.where(A12, lam, 1.0) * np.where(A23, mu, 1.0)
    top = np.argsort(-s, 1)[:, :K]
    hit = (top == W[:, None]).any(1)
    st = BUDGET // K // UNIT * UNIT
    return hit, np.where(hit, PY_ * st / 100.0, 0.0), st * K
hA, pA, inv = arm(LAM, MU); hB, pB, _ = arm(1.0, 1.0)
QS = [("2024Q3","2024-07-01","2024-09-30"),("2024Q4","2024-10-01","2024-12-31"),
      ("2025Q1","2025-01-01","2025-03-31"),("2025Q2","2025-04-01","2025-06-30"),
      ("2025Q3","2025-07-01","2025-09-30"),("2025Q4","2025-10-01","2025-12-31"),
      ("2026Q1","2026-01-01","2026-03-31"),("2026Q2","2026-04-01","2026-06-30"),
      ("2026Q3","2026-07-01","2026-08-26"),
      ("── 探索計","2024-07-01","2025-12-31"),("── 確認計","2026-01-01","2026-08-26")]
print("窓        n     的中(λ)  的中(素)   Δ的中 CI          ROI(λ)  ROI(素)   ΔROI CI")
for nm, lo, hi in QS:
    m = (D >= lo) & (D <= hi)
    days = sorted(set(D[m])); rng = np.random.default_rng(5); dh = []; dr = []
    dmap = {d: np.flatnonzero(m & (D == d)) for d in days}
    for _ in range(1500):
        sel = np.concatenate([dmap[d] for d in rng.choice(days, len(days), replace=True)])
        dh.append(hA[sel].mean() - hB[sel].mean())
        dr.append((pA[sel].sum() - pB[sel].sum()) / (len(sel) * inv))
    print(f"{nm:9s} {m.sum():6d} {hA[m].mean()*100:6.2f}% {hB[m].mean()*100:6.2f}%  "
          f"{np.mean(dh)*100:+5.2f}[{np.percentile(dh,2.5)*100:+5.2f},{np.percentile(dh,97.5)*100:+5.2f}]  "
          f"{pA[m].sum()/(m.sum()*inv)*100:6.1f}% {pB[m].sum()/(m.sum()*inv)*100:6.1f}%  "
          f"{np.mean(dr)*100:+5.1f}[{np.percentile(dr,2.5)*100:+5.1f},{np.percentile(dr,97.5)*100:+5.1f}]")
