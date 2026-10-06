#!/usr/bin/env python3
"""H21 目視確認: ①②③で売った買い目が実際に変わっているレースを選び、各点の 予測/120分前/60分前/最終 オッズと結果を出す。"""
from __future__ import annotations
import pickle, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import h21_core as K                                       # noqa: E402

H = K.H
recs, _ = K.load_window()
idx = {r["key"]: i for i, r in enumerate(recs)}
A = {n: pickle.load(open(H / f"arm_{n}.pkl", "rb")) for n in ("A1", "A2", "A3")}
SLOT = {"main": "main", "highpay": "sign", "lead": "lead"}


def sold_map(z):
    out = {}
    for d, (sold, _) in z["lineup"].items():
        for s in sold:
            out[s["key"]] = s
    return out


SM = {n: sold_map(A[n]) for n in A}


def legs_of(n, key):
    s = SM[n].get(key)
    if s is None:
        return None, None
    rec = A[n]["by_i"][idx[key]]
    b = rec[SLOT[s["slot"]]]
    return s, b


def show(key):
    i = idx[key]; r = recs[i]
    print(f"\n=== {key}  {r['date']}  {r['rtype']}  src={r['src']}  当たり {'-'.join(map(str,r['win']))}（三連単確定 {r['pay_tf']:.1f}倍 / 三連複確定 {r['odds_t3']:.1f}倍）")
    for lead in (120, 60):
        sn = r["snap"].get(lead)
        print(f"  {lead}分前の板: " + ("無し" if sn is None else f"{sn['type']} {sn['at']:%m-%d %H:%M}（発走の{sn['lead_min']:.0f}分前・有効組 {len(sn['tf'])}/210）"))
    po_t3, _ = K.S._fold_to_trio({K.PERMS[t]: float(r["PO1"][t]) for t in range(210)}, {})
    for n in ("A1", "A2", "A3"):
        s, b = legs_of(n, key)
        if s is None:
            print(f"  [{n}] 売っていない"); continue
        print(f"  [{n}] {s['slot']} / {b['plan']} / 点数 {b['n']} / 投資 {s['inv']:,.0f} / 払戻 {s['pay']:,.0f}")
    print(f"  {'組':<10}" + "".join(f"{n+'賭金':>8}" for n in A) + f"{'予測':>9}{'120分前':>9}{'60分前':>9}{'最終':>9}  当")
    union = []
    for n in A:
        s, b = legs_of(n, key)
        if b:
            for combo, stake, o in b["legs"]:
                if (combo, b["trio"]) not in [(u[0], u[1]) for u in union]:
                    union.append((combo, b["trio"]))
    for combo, trio in union:
        def od(src):
            if trio:
                return src["trio"].get(frozenset(combo)) if src else None
            return src["tf"].get(tuple(combo)) if src else None
        cells = []
        for n in A:
            s, b = legs_of(n, key)
            st = next((x[1] for x in (b["legs"] if b else []) if x[0] == combo and b["trio"] == trio), 0)
            cells.append(f"{st:>8}")
        pred = po_t3.get(frozenset(combo)) if trio else float(r["PO1"][K.PIDX[tuple(combo)]])
        s120 = od(r["snap"].get(120)); s60 = od(r["snap"].get(60))
        fin = od(r["final"])
        won = (frozenset(combo) == frozenset(r["win"])) if trio else (tuple(combo) == tuple(r["win"]))
        f = lambda v: f"{v:>9.1f}" if v else f"{'-':>9}"
        print(f"  {('=' if trio else '-').join(map(str,combo)):<10}" + "".join(cells) + f(pred) + f(s120) + f(s60) + f(fin) + ("  ★" if won else ""))


cands = []
for key in sorted(idx):
    ss = [SM[n].get(key) for n in A]
    if not all(ss):
        continue
    bs = [legs_of(n, key)[1] for n in A]
    sets = [frozenset((c, bb["trio"]) for c, _, _ in bb["legs"]) for bb in bs]
    if sets[0] != sets[1] and sets[0] != sets[2] and sets[1] != sets[2]:
        win = recs[idx[key]]["win"]
        hit = [any(((frozenset(c) == frozenset(win)) if bb["trio"] else tuple(c) == tuple(win)) for c, _, _ in bb["legs"]) for bb in bs]
        cands.append((key, hit, bs[0]["plan"], bs[1]["plan"], bs[2]["plan"], recs[idx[key]]["src"]))
print("①②③で買い目の集合が3通りに分かれたレース", len(cands))
print("  うち当たり目を含む腕がある:", sum(1 for c in cands if any(c[1])))
want = [next((c for c in cands if c[5] == "board" and any(c[1]) and c[2] == c[3] == c[4]), None),
        next((c for c in cands if c[5] == "db" and not any(c[1]) and c[2] == c[3] == c[4]), None)]
for c in want:
    if c:
        show(c[0])
