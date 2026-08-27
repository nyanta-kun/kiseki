#!/usr/bin/env python3
"""ライン型が勝つ機序 — PL型は食い違うとき何を選んでいるのか。"""
from __future__ import annotations
import itertools, os, sys
from collections import Counter
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3))
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
PW, P3, LG, LPOS = z["PW"], z["P3"], z["LG"], z["A_line_pos"]

def ax_pl(i):
    c = CANON[int(np.argmax(PROB[i]))]; return (c[0], c[1])
def strongest(i):
    best = None
    for g in set(LG[i]):
        if g in ("", "0"): continue
        mem = [c for c in range(1, 8) if LG[i][c - 1] == g]
        lead = [c for c in mem if LPOS[i][c - 1] == 1]; sec = [c for c in mem if LPOS[i][c - 1] == 2]
        if not lead or not sec: continue
        sc = sum(PW[i][c - 1] for c in mem)
        if best is None or sc > best[0]: best = (sc, lead[0], sec[0], g)
    return best

ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY))
cat = Counter(); ok_by = {}
for i in ii:
    b = strongest(i)
    if b is None: continue
    _, ld, sd, g = b
    a1, a2 = ax_pl(i)
    if (a1, a2) == (ld, sd): c = "一致"
    elif (a1, a2) == (sd, ld): c = "PLは 番手→先頭 に反転"
    elif a1 == ld: c = "PLは 先頭→別ラインの車"
    elif LG[i][a1-1] != g: c = "PLは 別ラインを1着に"
    else: c = "その他(同ライン3番手など)"
    cat[c] += 1
    w = CANON[int(WIN[i])]
    ok_by.setdefault(c, [0, 0, 0])
    ok_by[c][0] += 1
    ok_by[c][1] += 1 if (w[0], w[1]) == (ld, sd) else 0
    ok_by[c][2] += 1 if (w[0], w[1]) == (a1, a2) else 0
n = sum(cat.values())
print(f"n={n:,}")
for k, v in cat.most_common():
    a = ok_by[k]
    print(f"  {k:26s} {v/n*100:5.1f}%  二軸そろい: ライン型 {a[1]/a[0]*100:5.2f}% / PL型 {a[2]/a[0]*100:5.2f}%")

# 予測オッズ比較（同じ3着を流したときの1点あたり）
print("\n[同じ形で比べたときの予測オッズ中央値]")
CIDX = {c: j for j, c in enumerate(CANON)}
ol, op = [], []
for i in ii:
    b = strongest(i)
    if b is None: continue
    _, ld, sd, _ = b; a1, a2 = ax_pl(i)
    if (a1, a2) == (ld, sd): continue
    cl = max((PROB[i][CIDX[(ld, sd, x)]], x) for x in range(1, 8) if x not in (ld, sd))[1]
    cp = max((PROB[i][CIDX[(a1, a2, x)]], x) for x in range(1, 8) if x not in (a1, a2))[1]
    ol.append(PO[i][CIDX[(ld, sd, cl)]]); op.append(PO[i][CIDX[(a1, a2, cp)]])
print(f"  ライン型の最上位点 {np.median(ol):7.2f}倍 / PL型の最上位点 {np.median(op):7.2f}倍  (n={len(ol):,})")
