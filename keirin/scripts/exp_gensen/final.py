#!/usr/bin/env python3
"""最終メニュー: 同ライン隣接ボーナス(λ=2.0, μ=1.5)を入れた三連単の運用点。
確認は 2026（予測オッズ OOS）。7T3 の本番形も λ の有無で比較。"""
from __future__ import annotations
import itertools, os, sys
from collections import defaultdict
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE, RTYPE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"], z["RTYPE"]
LG, LPOS = z["LG"], z["A_line_pos"]
ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
A12 = np.zeros((len(ii), 210), bool); A23 = np.zeros((len(ii), 210), bool)
for r, i in enumerate(ii):
    def adj(a, b):
        return (LG[i][a-1] == LG[i][b-1] and LG[i][a-1] not in ("", "0") and LPOS[i][b-1] == LPOS[i][a-1] + 1)
    for t, (a, b, c) in enumerate(CANON):
        A12[r, t] = adj(a, b); A23[r, t] = adj(b, c)
S_LAM = PROB[ii] * np.where(A12, 2.0, 1.0) * np.where(A23, 1.5, 1.0)
S_RAW = PROB[ii]
W = WIN[ii]; PY_ = PAY[ii]; D = np.array([str(DATE[i]) for i in ii]); RT = RTYPE[ii]; PO_ = PO[ii]
KACHI = ("予選", "準決勝", "特一般", "チャレンジ予選", "チャレンジ準決勝", "特予選")

def run(S, minodds, k, pop, cap, lo, hi):
    m = (D >= lo) & (D <= hi)
    if pop: m = m & np.isin(RT, pop)
    byday = defaultdict(list)
    for r in np.flatnonzero(m):
        c = np.flatnonzero(PO_[r] >= minodds)
        if len(c) < k: continue
        c = c[np.argsort(-S[r][c])][:k]
        byday[D[r]].append((S[r][c].sum(), r, set(int(x) for x in c)))
    recs = []
    for d, lst in byday.items():
        lst.sort(key=lambda t: -t[0])
        for _, r, cs in (lst[:cap] if cap else lst):
            st = BUDGET // k // UNIT * UNIT
            recs.append((st * k, PY_[r] * st / 100.0 if int(W[r]) in cs else 0.0, d))
    if not recs: return None
    nd = len(byday); inv = sum(x[0] for x in recs); pay = sum(x[1] for x in recs)
    hs = sorted(x[1] for x in recs if x[1] > 0)
    return dict(pd=len(recs)/nd, hit=len(hs)/len(recs)*100, roi=pay/inv*100, med=median(hs) if hs else 0,
                b10=sum(1 for p in hs if p >= 100_000)/nd, b30=sum(1 for p in hs if p >= 300_000)/nd,
                b50=sum(1 for p in hs if p >= 500_000)/nd, b100=sum(1 for p in hs if p >= 1_000_000)/nd)

def line(lbl, S, mo, k, pop, cap):
    a = run(S, mo, k, pop, cap, "2026-01-01", "2026-08-26")
    if not a: return
    h1 = run(S, mo, k, pop, cap, "2026-01-01", "2026-04-30"); h2 = run(S, mo, k, pop, cap, "2026-05-01", "2026-08-26")
    e = run(S, mo, k, pop, cap, "2024-07-01", "2025-12-31")
    print(f"{lbl:34s} {a['pd']:6.2f} {a['hit']:6.2f}% {a['roi']:6.1f}({h1['roi']:5.1f}/{h2['roi']:5.1f}) "
          f"{a['med']:>9,.0f} {a['b10']:6.3f} {a['b30']:6.3f} {a['b50']:6.3f} {a['b100']:6.3f}"
          + (f"   |探索ROI {e['roi']:5.1f}%(オッズin-sample)" if e else ""))

print("構成                                件/日   的中    ROI(通/H1/H2)   払戻中央  10万+  30万+  50万+ 100万+ /日")
print("── 高的中枠（614 と同型）")
line("λ 帯なし 1点 全7車 上限20", S_LAM, 0, 1, None, 20)
line("素 帯なし 1点 全7車 上限20", S_RAW, 0, 1, None, 20)
line("λ 帯なし 1点 全7車 上限なし", S_LAM, 0, 1, None, None)
print("── 一撃枠")
line("λ 30倍+ 1点 全7車 上限5", S_LAM, 30, 1, None, 5)
line("λ 30倍+ 1点 全7車 上限10", S_LAM, 30, 1, None, 10)
line("素 30倍+ 1点 全7車 上限10", S_RAW, 30, 1, None, 10)
line("λ 50倍+ 1点 全7車 上限10", S_LAM, 50, 1, None, 10)
line("λ 30倍+ 1点 勝上系 上限5", S_LAM, 30, 1, KACHI, 5)
print("── 現行 7T3 の形（決勝・30倍+・5点）")
line("λ 7T3形", S_LAM, 30, 5, ("決勝", "チャレンジ決勝"), None)
line("素 7T3形", S_RAW, 30, 5, ("決勝", "チャレンジ決勝"), None)
