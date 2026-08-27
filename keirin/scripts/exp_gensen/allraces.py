#!/usr/bin/env python3
"""全7車レース上での三連単の組み立て比較（vintage板・2026窓）。
併せて「厳選AI(614)が選んだレース」かどうかで層別する。"""
from __future__ import annotations
import itertools, json, os, sys
from collections import defaultdict
from pathlib import Path
from statistics import median
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); os.chdir(REPO)
from src.strategy_wt import rank_7t1_select, rank_7t1_stakes  # noqa: E402

CANON = list(itertools.permutations(range(1, 8), 3))
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = list(z["KEY"]); PROB, PO, WIN, PAY = z["PROB"], z["PO"], z["WIN"], z["PAY"]
P3, PW, OK, DATE = z["P3"], z["PW"], z["OKPRED"], z["DATE"]

sel_keys = set()
G = Path(__file__).resolve().parent / "gensen" / "joined.jsonl"
for l in G.open(encoding="utf-8"):
    r = json.loads(l); sel_keys.add(r["race_key"])

lo, hi = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else ("2026-01-01", "2026-08-26")
ii = [i for i in range(len(KEY)) if lo <= DATE[i] <= hi and OK[i]
      and int(WIN[i]) >= 0 and np.isfinite(PAY[i])]
print(f"母集団 7車 {len(ii):,}R  {lo}〜{hi}  日数 {len(set(DATE[i] for i in ii))}")

def run(strategy, ii):
    out = []
    for i in ii:
        pts = strategy(i)
        if not pts: continue
        w = int(WIN[i]); combo = CANON[w]
        pay = PAY[i] * pts.get(combo, 0) / 100.0
        out.append((sum(pts.values()), pay, len(pts), DATE[i]))
    return out

def show(name, out):
    if not out: return
    nd = len(set(o[3] for o in out))
    inv = sum(o[0] for o in out); pay = sum(o[1] for o in out)
    hits = sorted(o[1] for o in out if o[1] > 0)
    big = {t: sum(1 for p in hits if p >= t) for t in (30_000, 100_000, 300_000, 1_000_000)}
    print(f"  {name:30s} {len(out)/nd:5.1f}件/日 点{np.mean([o[2] for o in out]):4.2f} "
          f"的中 {len(hits)/len(out)*100:5.2f}% ROI {pay/inv*100:6.1f}% 中央 {median(hits) if hits else 0:>8,.0f} "
          + " ".join(f"{t//10000}万+{big[t]/nd:6.3f}/日" for t in big))

def s_top(n):
    def f(i):
        j = list(np.argsort(-PROB[i])[:n])
        s = BUDGET // n // UNIT * UNIT
        return {CANON[t]: s for t in j}
    return f

def s_odds_top(minodds, n):
    def f(i):
        c = [t for t in range(210) if PO[i, t] >= minodds]
        if len(c) < n: return None
        c.sort(key=lambda t: -PROB[i, t]); c = c[:n]
        s = BUDGET // n // UNIT * UNIT
        return {CANON[t]: s for t in c}
    return f

def s_7t1(i):
    p3 = {c + 1: float(P3[i, c]) for c in range(7)}
    pw = {c + 1: float(PW[i, c]) for c in range(7)}
    po = {CANON[t]: float(PO[i, t]) for t in range(210)}
    sel = rank_7t1_select(p3, pw, po)
    if not sel: return None
    st = rank_7t1_stakes(sel[2])
    return {tuple(int(x) for x in leg.split("-")): st[leg] for leg in sel[2]}

STR = [("PL top1・1点", s_top(1)), ("PL top2・2点", s_top(2)), ("PL top3・3点", s_top(3)),
       ("PL top5・5点", s_top(5)),
       ("予測10倍+ PL上位1点", s_odds_top(10, 1)), ("予測20倍+ PL上位1点", s_odds_top(20, 1)),
       ("予測30倍+ PL上位1点", s_odds_top(30, 1)), ("予測50倍+ PL上位1点", s_odds_top(50, 1)),
       ("予測30倍+ PL上位5点", s_odds_top(30, 5)),
       ("7T1の組み立て(目標15万)", s_7t1)]

for label, sub in (("全7車", ii), ("614が選んだレース", [i for i in ii if KEY[i] in sel_keys]),
                   ("614が選ばなかった", [i for i in ii if KEY[i] not in sel_keys])):
    print(f"\n[{label}] n={len(sub):,}")
    for nm, f in STR:
        show(nm, run(f, sub))
