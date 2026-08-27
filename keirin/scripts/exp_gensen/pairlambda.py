#!/usr/bin/env python3
"""二軸（順序対）に『同ライン隣接ボーナス λ』を入れて掃引する。
  score(a1,a2) = Σ_c PL(a1,a2,c)  ×  λ  if a2 が a1 の直後（同ライン line_pos+1）
探索: 2024-07〜2025-12 / 確認: 2026-01〜08。3着は PL 上位k点、1レース1万円をk等分。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: j for j, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, WIN, PAY, OK, DATE = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
LG, LPOS = z["LG"], z["A_line_pos"]

ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
# 事前計算: 各レースの順序対ごとの Σ_c PL と 隣接フラグ
PAIRS = [(a, b) for a in range(1, 8) for b in range(1, 8) if a != b]
PSUM = np.zeros((len(ii), 42), np.float32)
ADJ = np.zeros((len(ii), 42), bool)
THIRD = np.zeros((len(ii), 42, 5), np.int8)
for r, i in enumerate(ii):
    for j, (a, b) in enumerate(PAIRS):
        cs = sorted(((PROB[i][CIDX[(a, b, c)]], c) for c in range(1, 8) if c not in (a, b)), key=lambda t: -t[0])
        PSUM[r, j] = sum(t[0] for t in cs)
        THIRD[r, j] = [t[1] for t in cs]
        ADJ[r, j] = (LG[i][a-1] == LG[i][b-1] and LG[i][a-1] not in ("", "0")
                     and LPOS[i][b-1] == LPOS[i][a-1] + 1)
W = np.array([CANON[int(WIN[i])] for i in ii])
D = np.array([str(DATE[i]) for i in ii])
PY_ = PAY[ii]

def run(lam, k, mask):
    sc = PSUM * np.where(ADJ, lam, 1.0)
    j = sc.argmax(1)
    a1 = np.array([PAIRS[x][0] for x in j]); a2 = np.array([PAIRS[x][1] for x in j])
    third = THIRD[np.arange(len(ii)), j, :k]
    s = BUDGET // k // UNIT * UNIT
    hit = (W[:, 0] == a1) & (W[:, 1] == a2) & (third == W[:, 2][:, None]).any(1)
    pay = np.where(hit & mask, PY_ * s / 100.0, 0.0)
    m = mask
    nd = len(set(D[m]))
    roi = pay[m].sum() / (m.sum() * s * k) * 100
    hs = sorted(pay[m][pay[m] > 0])
    ok2 = ((W[:, 0] == a1) & (W[:, 1] == a2))[m].mean() * 100
    return dict(roi=roi, hit=len(hs)/m.sum()*100, ok2=ok2, med=median(hs) if hs else 0,
                b10=sum(1 for p in hs if p >= 100_000)/nd)

EX = (D >= "2024-07-01") & (D <= "2025-12-31")
CO = (D >= "2026-01-01") & (D <= "2026-08-26")
print("λ      k   [探索 2024H2-2025] 二軸  的中   ROI  | [確認 2026] 二軸  的中   ROI   中央   10万+/日")
for k in (1, 2, 5):
    for lam in (1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0, 2.5, 3.0, 1e9):
        a = run(lam, k, EX); b = run(lam, k, CO)
        tag = "∞(常にライン)" if lam > 1e6 else f"{lam:.2f}"
        print(f"{tag:>12s} {k}  {a['ok2']:5.2f}% {a['hit']:5.2f}% {a['roi']:6.1f}%  |"
              f" {b['ok2']:5.2f}% {b['hit']:5.2f}% {b['roi']:6.1f}% {b['med']:>8,.0f} {b['b10']:6.3f}")
    print()
