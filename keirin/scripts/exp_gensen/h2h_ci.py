#!/usr/bin/env python3
"""彼ら vs 自社の同一レース対決に日次ブートストラップCIを付ける。"""
from __future__ import annotations
import itertools, json, os, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); os.chdir(REPO)
from src.strategy_wt import rank_7t1_select, rank_7t1_stakes  # noqa: E402
CANON = list(itertools.permutations(range(1, 8), 3)); BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = list(z["KEY"]); idx = {k: i for i, k in enumerate(KEY)}
PROB, PO, WIN, PAY, P3, PW, OK, DATE = (z["PROB"], z["PO"], z["WIN"], z["PAY"],
                                        z["P3"], z["PW"], z["OKPRED"], z["DATE"])
rows = [json.loads(l) for l in (Path(__file__).resolve().parent / "gensen" / "joined.jsonl").open(encoding="utf-8")]

def their_points(r):
    pts = {}
    for l in r["legs"]:
        if l["bet_type"] != "3連単" or len(l["cols"]) < 3: continue
        for c in itertools.product(*l["cols"]):
            if len(set(c)) == 3: pts[c] = pts.get(c, 0) + (l["unit"] or 0)
    return pts

arms = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))  # arm -> date -> [inv,pay,hit]
ndays = set()
for r in rows:
    i = idx.get(r["race_key"])
    if i is None or not OK[i] or int(WIN[i]) < 0 or not np.isfinite(PAY[i]): continue
    pts = their_points(r)
    if not pts: continue
    d = str(DATE[i]); ndays.add(d)
    win = CANON[int(WIN[i])]
    def add(arm, pp):
        inv = sum(pp.values()); pay = PAY[i] * pp.get(win, 0) / 100.0
        a = arms[arm][d]; a[0] += inv; a[1] += pay; a[2] += 1 if pay > 0 else 0
    add("彼ら", pts)
    add("PLtop1", {CANON[int(np.argmax(PROB[i]))]: BUDGET})
    j2 = list(np.argsort(-PROB[i])[:2]); add("PLtop2", {CANON[t]: BUDGET // 2 for t in j2})
    p3 = {c + 1: float(P3[i, c]) for c in range(7)}; pw = {c + 1: float(PW[i, c]) for c in range(7)}
    po = {CANON[t]: float(PO[i, t]) for t in range(210)}
    sel = rank_7t1_select(p3, pw, po)
    if sel:
        st = rank_7t1_stakes(sel[2])
        add("7T1", {tuple(int(x) for x in leg.split("-")): st[leg] for leg in sel[2]})

days = sorted(ndays)
print(f"対象 {len(days)}日")
def agg(arm, ds):
    inv = sum(arms[arm][d][0] for d in ds); pay = sum(arms[arm][d][1] for d in ds)
    n = sum(1 for d in ds for _ in [0])  # unused
    return pay / inv * 100 if inv else 0.0
def hitrate(arm, ds):
    h = sum(arms[arm][d][2] for d in ds); n = sum(1 for d in ds if arms[arm][d][0] > 0)
    return h
rng = np.random.default_rng(0)
B = 3000
base = {a: agg(a, days) for a in arms}
print("\n     腕        ROI      95%CI          彼らとの差 95%CI")
sam = {a: [] for a in arms}; dif = {a: [] for a in arms}
for _ in range(B):
    ds = list(rng.choice(days, len(days), replace=True))
    r0 = agg("彼ら", ds)
    for a in arms:
        v = agg(a, ds); sam[a].append(v); dif[a].append(v - r0)
for a in ("彼ら", "PLtop1", "PLtop2", "7T1"):
    lo, hi = np.percentile(sam[a], [2.5, 97.5]); dlo, dhi = np.percentile(dif[a], [2.5, 97.5])
    print(f"  {a:8s} {base[a]:7.1f}%  [{lo:6.1f},{hi:6.1f}]   {base[a]-base['彼ら']:+6.1f}pt [{dlo:+6.1f},{dhi:+6.1f}]")
