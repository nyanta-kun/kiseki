#!/usr/bin/env python3
"""ライン型二軸 vs PL型二軸 — 全期間プールと『食い違うレースだけ』の直接対決。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: i for i, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, WIN, PAY, OK, DATE = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
PW, LG, LPOS = z["PW"], z["LG"], z["A_line_pos"]

def ax_pl(i):
    c = CANON[int(np.argmax(PROB[i]))]; return (c[0], c[1])
def ax_line(i):
    best = None
    for g in set(LG[i]):
        if g in ("", "0"): continue
        mem = [c for c in range(1, 8) if LG[i][c - 1] == g]
        lead = [c for c in mem if LPOS[i][c - 1] == 1]; sec = [c for c in mem if LPOS[i][c - 1] == 2]
        if not lead or not sec: continue
        sc = sum(PW[i][c - 1] for c in mem)
        if best is None or sc > best[0]: best = (sc, lead[0], sec[0])
    return None if best is None else (best[1], best[2])
def pick(i, ax, k):
    a1, a2 = ax
    c = sorted(((PROB[i][CIDX[(a1, a2, x)]], x) for x in range(1, 8) if x not in (a1, a2)), key=lambda t: -t[0])
    return [t[1] for t in c[:k]]

base = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
K = 2
recs = []
for i in base:
    al, ap = ax_line(i), ax_pl(i)
    if al is None: continue
    w = CANON[int(WIN[i])]
    s = BUDGET // K // UNIT * UNIT
    pl_ = PAY[i]*s/100.0 if (w[0], w[1]) == ap and w[2] in pick(i, ap, K) else 0.0
    ln_ = PAY[i]*s/100.0 if (w[0], w[1]) == al and w[2] in pick(i, al, K) else 0.0
    recs.append((str(DATE[i]), al == ap, ln_, pl_, s*K,
                 (w[0], w[1]) == al, (w[0], w[1]) == ap))
same = sum(1 for r in recs if r[1])
print(f"n={len(recs):,}  二軸が一致 {same/len(recs)*100:.1f}%  食い違い {100-same/len(recs)*100:.1f}%")
def rep(lbl, rr):
    if not rr: return
    nd = len(set(r[0] for r in rr)); inv = sum(r[4] for r in rr)
    for nm, ix, okx in (("ライン型", 2, 5), ("PL型", 3, 6)):
        pay = sum(r[ix] for r in rr); hits = sorted(r[ix] for r in rr if r[ix] > 0)
        ok2 = sum(1 for r in rr if r[okx])
        print(f"  {lbl:12s} {nm:6s} n={len(rr):6d} 二軸そろい {ok2/len(rr)*100:5.2f}% 的中 {len(hits)/len(rr)*100:5.2f}%"
              f" ROI {pay/inv*100:6.1f}% 中央 {median(hits) if hits else 0:>8,.0f}"
              f" 10万+{sum(1 for p in hits if p>=100_000)/nd:6.3f}/日")
    rng = np.random.default_rng(3); days = sorted(set(r[0] for r in rr)); byd = {}
    for r in rr: byd.setdefault(r[0], []).append(r)
    dif = []
    for _ in range(3000):
        ds = rng.choice(days, len(days), replace=True)
        a = sum(x[2] for d in ds for x in byd[d]); b = sum(x[3] for d in ds for x in byd[d])
        iv = sum(x[4] for d in ds for x in byd[d])
        dif.append((a - b) / iv * 100)
    print(f"  {'':12s} Δ(ライン−PL) {np.mean(dif):+6.1f}pt CI[{np.percentile(dif,2.5):+6.1f},{np.percentile(dif,97.5):+6.1f}]")
rep("全期間", recs)
rep("食い違いのみ", [r for r in recs if not r[1]])
for lo, hi, lbl in (("2024-07-01","2024-12-31","24H2"),("2025-01-01","2025-06-30","25H1"),
                    ("2025-07-01","2025-12-31","25H2"),("2026-01-01","2026-04-30","26H1"),
                    ("2026-05-01","2026-08-26","26H2")):
    rep(lbl+"食違", [r for r in recs if not r[1] and lo <= r[0] <= hi])
