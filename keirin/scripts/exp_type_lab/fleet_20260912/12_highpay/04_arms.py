#!/usr/bin/env python3
"""腕⑥a / ⑥b / ② / ⑤ / ⑦ を制約込みで測る。

基準は腕①（現行 = 5本・B/C/D・供給は日次上限落ちだけ）。
差は**日単位** paired bootstrap の 95%CI（純増の腕はレース単位で対応が取れない）。
"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

ALL6 = tuple("ABCDEF")
BCD = ("B", "C", "D")
LAM = float(sys.argv[1]) if len(sys.argv) > 1 else 0.50

ARMS = [
    ("① 現行 (5本・BCD・cap)",        dict(hp_slots=5,  hp_types=BCD,  supply=("cap",))),
    ("⑥a 10本・BCD・cap",            dict(hp_slots=10, hp_types=BCD,  supply=("cap",))),
    ("⑥a' 10本・全6型・cap",          dict(hp_slots=10, hp_types=ALL6, supply=("cap",))),
    ("⑥b 10本・BCD・cap+axis",       dict(hp_slots=10, hp_types=BCD,  supply=("cap", "axis"))),
    ("⑥b' 10本・全6型・cap+axis",     dict(hp_slots=10, hp_types=ALL6, supply=("cap", "axis"))),
    ("⑦ 5本・全6型・cap+axis",        dict(hp_slots=5,  hp_types=ALL6, supply=("cap", "axis"))),
    ("② ⑥b'×pw_gap12下位25%",        dict(hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                                          layer="pw_gap12_lo25")),
    ("②' ⑥b'×axis_sum下位25%",       dict(hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                                          layer="axis_sum_lo25")),
    ("⑥b'-bust15 (全枠 bust@15万)",   dict(hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                                          product="bust15")),
    ("⑥b'-sign (全枠 sign@15万)",     dict(hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                                          product="sign")),
    ("⑥b'-big (全枠 big@40万)",       dict(hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                                          product="big")),
    ("（参考）全量・全6型・cap+axis",   dict(hp_slots=99, hp_types=ALL6, supply=("cap", "axis"))),
]

for win in ("explore", "confirm"):
    nd = L.ndays(win)
    days = sorted({r["date"] for r in L.load() if r["win"] == win})
    base = L.simulate(win, hp_slots=5, hp_types=BCD, supply=("cap",), frac=LAM)
    print(f"\n{'='*130}\n[{win}]  λ(上限の分母係数)={LAM}  {nd}日\n{'='*130}")
    print(L.HEAD)
    res = {}
    for nm, kw in ARMS:
        r = L.simulate(win, frac=LAM, **kw)
        res[nm] = r
        o = Counter(x["origin"] for x in r)
        hp = o["hp:cap"] + o["hp:axis"]
        slots = kw["hp_slots"]
        fill = f" 充填{hp/min(slots,99):5.2f}/{slots}本={hp/nd/slots*100:5.1f}%" if slots else ""
        print(L.line(nm, L.kpi(r, nd))
              + f"  |高額枠 {hp/nd:5.2f}/日(cap {o['hp:cap']/nd:.2f}+axis {o['hp:axis']/nd:.2f})"
              + (f" 充填率{hp/nd/slots*100:5.1f}%" if slots and slots < 99 else ""))
    print("\n  ── 腕① との差（日単位 paired bootstrap 95%CI）──")
    print("  {:30s} {:>26s} {:>26s} {:>26s} {:>22s}".format(
        "腕", "Δ件/日", "Δ表示的中pt", "Δ10万+/日", "Δ30万+/日"))
    for nm, _kw in ARMS:
        if nm.startswith("①"):
            continue
        b = L.dboot(base, res[nm], days)
        print("  {:30s} {:>26s} {:>26s} {:>26s} {:>22s}".format(
            nm, L.ci(b["perday"]), L.ci(b["shown"]), L.ci(b["big"]), L.ci(b["b30"])))
    print("\n  ── 既存商品が減っていないか（純増の検算）──")
    for nm, _kw in ARMS:
        c = Counter(x["origin"] for x in res[nm])
        print(f"    {nm:30s} 通常 {c['normal']/nd:6.3f}  枠外 {c['exempt']/nd:6.3f}"
              f"  高額枠 {(c['hp:cap']+c['hp:axis'])/nd:6.3f}")
