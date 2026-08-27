#!/usr/bin/env python3
"""λ=2.5 の同ライン隣接ボーナスの効果に日次ブートストラップCIを付ける。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: j for j, c in enumerate(CANON)}
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, WIN, PAY, OK, DATE, LG, LPOS = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"], z["LG"], z["A_line_pos"]
ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
PAIRS = [(a, b) for a in range(1, 8) for b in range(1, 8) if a != b]
PSUM = np.zeros((len(ii), 42), np.float32); ADJ = np.zeros((len(ii), 42), bool)
for r, i in enumerate(ii):
    for j, (a, b) in enumerate(PAIRS):
        PSUM[r, j] = sum(PROB[i][CIDX[(a, b, c)]] for c in range(1, 8) if c not in (a, b))
        ADJ[r, j] = (LG[i][a-1] == LG[i][b-1] and LG[i][a-1] not in ("", "0")
                     and LPOS[i][b-1] == LPOS[i][a-1] + 1)
W = np.array([CANON[int(WIN[i])] for i in ii]); D = np.array([str(DATE[i]) for i in ii])
def ok2(lam):
    j = (PSUM * np.where(ADJ, lam, 1.0)).argmax(1)
    a1 = np.array([PAIRS[x][0] for x in j]); a2 = np.array([PAIRS[x][1] for x in j])
    return (W[:, 0] == a1) & (W[:, 1] == a2)
A, B = ok2(2.5), ok2(1.0)
for lbl, m in (("探索 2024-07〜2025-12", (D >= "2024-07-01") & (D <= "2025-12-31")),
               ("確認 2026-01〜08", (D >= "2026-01-01") & (D <= "2026-08-26"))):
    days = sorted(set(D[m])); rng = np.random.default_rng(11); dif = []
    for _ in range(3000):
        cnt = {}
        for d in rng.choice(days, len(days), replace=True): cnt[d] = cnt.get(d, 0) + 1
        w = np.zeros(len(ii))
        for d, v in cnt.items(): w[D == d] = v
        w = w * m
        dif.append(((A * w).sum() - (B * w).sum()) / w.sum() * 100)
    print(f"{lbl}: 二軸そろい λ2.5 {A[m].mean()*100:5.2f}% / λ1.0 {B[m].mean()*100:5.2f}% "
          f" Δ {np.mean(dif):+5.2f}pt CI[{np.percentile(dif,2.5):+5.2f},{np.percentile(dif,97.5):+5.2f}]"
          f"  変更されるレース {(( (PSUM*np.where(ADJ,2.5,1.0)).argmax(1) != (PSUM).argmax(1) )[m]).mean()*100:.1f}%")
