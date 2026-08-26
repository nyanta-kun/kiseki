#!/usr/bin/env python3
"""二軸（1着・2着の固定）の選び方を型ごとに比較する（vintage板・2026）。
3着は残り5車のうち PL 確率上位から k 車。1レース10,000円をk等分。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: i for i, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
P3, PW, LG, LPOS, LEAD = z["P3"], z["PW"], z["LG"], z["A_line_pos"], z["A_is_line_leader"]

ii = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY) &
                    (DATE >= "2026-01-01") & (DATE <= "2026-08-26"))
print(f"母集団 {len(ii):,}R / {len(set(DATE[ii]))}日")

def axes_pl(i):
    c = CANON[int(np.argmax(PROB[i]))]; return c[0], c[1]
def axes_pw(i):
    o = np.argsort(-PW[i]) + 1; return int(o[0]), int(o[1])
def axes_p3(i):
    o = np.argsort(-P3[i]) + 1; return int(o[0]), int(o[1])
def axes_line(i):
    """614型: 最有力車のラインの『先頭 → 番手』。番手が居なければ None。"""
    top = int(np.argmax(PW[i])) + 1
    g = LG[i][top - 1]
    mem = [c for c in range(1, 8) if LG[i][c - 1] == g and g not in ("", "0")]
    if len(mem) < 2: return None
    lead = [c for c in mem if LPOS[i][c - 1] == 1]
    second = [c for c in mem if LPOS[i][c - 1] == 2]
    if not lead or not second: return None
    return lead[0], second[0]
def axes_line_strict(i):
    """先頭が pw1位でなくても、最も強いラインの先頭→番手（ライン内 pw 合計が最大）。"""
    best = None
    for g in set(LG[i]):
        if g in ("", "0"): continue
        mem = [c for c in range(1, 8) if LG[i][c - 1] == g]
        if len(mem) < 2: continue
        lead = [c for c in mem if LPOS[i][c - 1] == 1]; sec = [c for c in mem if LPOS[i][c - 1] == 2]
        if not lead or not sec: continue
        sc = sum(PW[i][c - 1] for c in mem)
        if best is None or sc > best[0]: best = (sc, lead[0], sec[0])
    return None if best is None else (best[1], best[2])

def evaluate(name, fax, k, minodds=0.0):
    out = []; ok2 = 0; n = 0
    for i in ii:
        ax = fax(i)
        if ax is None: continue
        a1, a2 = ax
        if a1 == a2: continue
        n += 1
        w = CANON[int(WIN[i])]
        if w[0] == a1 and w[1] == a2: ok2 += 1
        cand = [(PROB[i][CIDX[(a1, a2, c)]], c) for c in range(1, 8) if c not in (a1, a2)]
        cand = [t for t in cand if PO[i][CIDX[(a1, a2, t[1])]] >= minodds]
        if len(cand) < k: continue
        cand.sort(key=lambda t: -t[0]); pick = [t[1] for t in cand[:k]]
        s = BUDGET // k // UNIT * UNIT
        pay = PAY[i] * s / 100.0 if (w[0] == a1 and w[1] == a2 and w[2] in pick) else 0.0
        out.append((s * k, pay, str(DATE[i])))
    nd = len(set(o[2] for o in out)); inv = sum(o[0] for o in out); pay = sum(o[1] for o in out)
    hits = sorted(o[1] for o in out if o[1] > 0)
    print(f"  {name:22s} k={k} 二軸そろい {ok2/n*100:5.2f}%  {len(out)/nd:5.2f}件/日 "
          f"的中 {len(hits)/len(out)*100:5.2f}% ROI {pay/inv*100:6.1f}% 中央 {median(hits) if hits else 0:>8,.0f} "
          f"10万+{sum(1 for p in hits if p>=100_000)/nd:6.3f}/日 30万+{sum(1 for p in hits if p>=300_000)/nd:6.3f}/日")

for k in (1, 2, 5):
    print(f"\n--- 3着 {k}点 ---")
    for nm, f in (("PL最上位の1-2着", axes_pl), ("pw 1位→2位", axes_pw), ("p3 1位→2位", axes_p3),
                  ("614型 pw1位のライン", axes_line), ("最強ライン 先頭→番手", axes_line_strict)):
        evaluate(nm, f, k)
