#!/usr/bin/env python3
"""提案する三連単「一撃枠」の運用点 — ライン二軸(λ) × 1点集中 × 高配当帯 × 日次上限。
確認は 2026（予測オッズ OOS）。H1/H2 でも割る。"""
from __future__ import annotations
import itertools, os, sys
from collections import defaultdict
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: j for j, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE, RTYPE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"], z["RTYPE"]
LG, LPOS = z["LG"], z["A_line_pos"]
KACHIAGARI = ("予選", "準決勝", "特一般", "チャレンジ予選", "チャレンジ準決勝", "特予選")

def pair_scores(i, lam):
    out = []
    for a in range(1, 8):
        for b in range(1, 8):
            if a == b: continue
            adj = (LG[i][a-1] == LG[i][b-1] and LG[i][a-1] not in ("", "0")
                   and LPOS[i][b-1] == LPOS[i][a-1] + 1)
            s = sum(PROB[i][CIDX[(a, b, c)]] for c in range(1, 8) if c not in (a, b))
            out.append((s * (lam if adj else 1.0), a, b))
    out.sort(key=lambda t: -t[0]); return out

def build(i, lam, minodds, k):
    for _, a1, a2 in pair_scores(i, lam)[:1]:
        cand = [(PROB[i][CIDX[(a1, a2, c)]], c) for c in range(1, 8) if c not in (a1, a2)
                and PO[i][CIDX[(a1, a2, c)]] >= minodds]
        if len(cand) < k: return None
        cand.sort(key=lambda t: -t[0])
        return [(a1, a2, c) for _, c in cand[:k]], sum(t[0] for t in cand[:k])
    return None

def evaluate(lam, minodds, k, pop, cap, lo, hi, verbose=True):
    byday = defaultdict(list)
    for i in np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY) & (DATE >= lo) & (DATE <= hi)):
        if pop and RTYPE[i] not in pop: continue
        b = build(i, lam, minodds, k)
        if b: byday[str(DATE[i])].append((b[1], i, b[0]))
    recs = []
    for d, lst in byday.items():
        lst.sort(key=lambda t: -t[0])
        for _, i, legs in (lst[:cap] if cap else lst):
            s = BUDGET // k // UNIT * UNIT
            w = CANON[int(WIN[i])]
            recs.append((s * k, PAY[i] * s / 100.0 if w in legs else 0.0, d))
    if not recs: return None
    nd = len(byday); inv = sum(r[0] for r in recs); pay = sum(r[1] for r in recs)
    hs = sorted(r[1] for r in recs if r[1] > 0)
    return dict(perday=len(recs)/nd, hit=len(hs)/len(recs)*100, roi=pay/inv*100,
                med=median(hs) if hs else 0, n=len(recs),
                b10=sum(1 for p in hs if p >= 100_000)/nd, b30=sum(1 for p in hs if p >= 300_000)/nd,
                b50=sum(1 for p in hs if p >= 500_000)/nd, b100=sum(1 for p in hs if p >= 1_000_000)/nd)

print("構成                                件/日  的中   ROI(通/H1/H2)      中央    10万+ 30万+ 50万+ 100万+ (件/日)")
CFG = [(2.5, 30, 1, None, 5), (2.5, 30, 1, None, 10), (2.5, 50, 1, None, 5), (2.5, 50, 1, None, 10),
       (2.5, 50, 1, None, None), (2.5, 30, 1, KACHIAGARI, 5), (2.5, 50, 1, KACHIAGARI, 5),
       (2.5, 50, 1, KACHIAGARI, 10), (2.5, 100, 1, None, 5), (2.5, 100, 1, None, 10),
       (2.5, 30, 2, None, 5), (2.5, 50, 2, None, 5), (2.5, 30, 2, None, 10),
       (1.0, 50, 1, None, 5), (1.0, 30, 1, None, 10),
       (2.5, 15, 1, None, 10), (2.5, 0, 1, None, 10), (2.5, 0, 1, None, 20)]
for lam, mo, k, pop, cap in CFG:
    a = evaluate(lam, mo, k, pop, cap, "2026-01-01", "2026-08-26")
    if not a: continue
    h1 = evaluate(lam, mo, k, pop, cap, "2026-01-01", "2026-04-30")
    h2 = evaluate(lam, mo, k, pop, cap, "2026-05-01", "2026-08-26")
    pn = "勝上" if pop else "全7車"
    print(f"λ{lam} {mo:3d}倍+ {k}点 {pn:4s} 上限{str(cap or '無'):>3s} {a['perday']:6.2f} {a['hit']:5.2f}% "
          f"{a['roi']:6.1f}/{h1['roi']:5.1f}/{h2['roi']:5.1f} {a['med']:>9,.0f} "
          f"{a['b10']:6.3f} {a['b30']:6.3f} {a['b50']:6.3f} {a['b100']:6.3f}")
