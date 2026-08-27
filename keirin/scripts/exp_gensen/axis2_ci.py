#!/usr/bin/env python3
"""二軸の型の差（最強ライン先頭→番手 vs PL最上位）を窓分割＋日次ブートストラップで検証。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: i for i, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
PW, LG, LPOS = z["PW"], z["LG"], z["A_line_pos"]

def ax_pl(i):
    c = CANON[int(np.argmax(PROB[i]))]; return c[0], c[1]
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

def legs(i, ax, k):
    a1, a2 = ax
    cand = sorted(((PROB[i][CIDX[(a1, a2, c)]], c) for c in range(1, 8) if c not in (a1, a2)), key=lambda t: -t[0])
    return [t[1] for t in cand[:k]]

def series(fax, k, mask):
    inv = {}; pay = {}; hit = {}
    for i in np.flatnonzero(mask):
        ax = fax(i)
        if ax is None or ax[0] == ax[1]: continue
        p = legs(i, ax, k); s = BUDGET // k // UNIT * UNIT
        w = CANON[int(WIN[i])]
        v = PAY[i] * s / 100.0 if (w[0], w[1]) == ax and w[2] in p else 0.0
        d = str(DATE[i]); inv[d] = inv.get(d, 0) + s * k; pay[d] = pay.get(d, 0) + v
        hit[d] = hit.get(d, 0) + (1 if v > 0 else 0)
    return inv, pay

base = OK & (WIN >= 0) & np.isfinite(PAY)
for lbl, lo, hi in (("2026 通", "2026-01-01", "2026-08-26"),
                    ("2026 H1", "2026-01-01", "2026-04-30"),
                    ("2026 H2", "2026-05-01", "2026-08-26"),
                    ("2024-25(オッズin-sample・参考)", "2024-07-01", "2025-12-31")):
    m = base & (DATE >= lo) & (DATE <= hi)
    print(f"\n[{lbl}]")
    for k in (1, 2, 5):
        ia, pa = series(ax_line, k, m); ib, pb = series(ax_pl, k, m)
        days = sorted(set(ia) & set(ib))
        ra = sum(pa[d] for d in days) / sum(ia[d] for d in days) * 100
        rb = sum(pb[d] for d in days) / sum(ib[d] for d in days) * 100
        rng = np.random.default_rng(7); dif = []
        for _ in range(3000):
            ds = rng.choice(days, len(days), replace=True)
            A = sum(pa[d] for d in ds) / sum(ia[d] for d in ds)
            B = sum(pb[d] for d in ds) / sum(ib[d] for d in ds)
            dif.append((A - B) * 100)
        lo_, hi_ = np.percentile(dif, [2.5, 97.5])
        print(f"  k={k}  ライン型 {ra:6.1f}%  PL型 {rb:6.1f}%   Δ {ra-rb:+6.1f}pt CI[{lo_:+6.1f},{hi_:+6.1f}]")
