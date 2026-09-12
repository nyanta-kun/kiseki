#!/usr/bin/env python3
"""実装の3点（①定数 5→10 ②軸ゲート落ちを供給源に ③HIGHPAY_TYPES に型F）を
**1つずつ入れて**どれが効いているかを分解する。商品（`alt`/`bust15`）も掛ける。"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

ALL6 = tuple("ABCDEF"); BCD = ("B", "C", "D"); BCDF = ("B", "C", "D", "F")
LAM = float(sys.argv[1]) if len(sys.argv) > 1 else 0.50
PROD = sys.argv[2] if len(sys.argv) > 2 else "alt"

ARMS = [
    ("基準 ①現行 5本/BCD/cap",            5,  BCD,  ("cap",)),
    ("+① 定数だけ 10本/BCD/cap",          10, BCD,  ("cap",)),
    ("+② 軸供給だけ 5本/BCD/cap+axis",     5,  BCD,  ("cap", "axis")),
    ("+③ 型Fだけ 5本/BCDF/cap",           5,  BCDF, ("cap",)),
    ("+③' 全6型だけ 5本/全6型/cap",         5,  ALL6, ("cap",)),
    ("①+② 10本/BCD/cap+axis",           10, BCD,  ("cap", "axis")),
    ("①+③ 10本/BCDF/cap",               10, BCDF, ("cap",)),
    ("②+③ 5本/BCDF/cap+axis",            5,  BCDF, ("cap", "axis")),
    ("①+②+③ 10本/BCDF/cap+axis",        10, BCDF, ("cap", "axis")),
    ("①+②+③' 10本/全6型/cap+axis",       10, ALL6, ("cap", "axis")),
]

for win in ("explore", "confirm"):
    nd = L.ndays(win); days = sorted({r["date"] for r in L.load() if r["win"] == win})
    base = L.simulate(win, hp_slots=5, hp_types=BCD, supply=("cap",), frac=LAM, product=PROD)
    print(f"\n{'='*128}\n[{win}] λ={LAM} 商品={PROD}  {nd}日\n{'='*128}")
    print(L.HEAD)
    out = {}
    for nm, sl, tp, sp in ARMS:
        r = L.simulate(win, hp_slots=sl, hp_types=tp, supply=sp, frac=LAM, product=PROD)
        out[nm] = (r, sl)
        o = Counter(x["origin"] for x in r); hp = o["hp:cap"] + o["hp:axis"]
        print(L.line(nm, L.kpi(r, nd))
              + f"  |枠 {hp/nd:5.2f}/{sl}本 充填{hp/nd/sl*100:5.1f}%")
    print("\n  ── 基準との差（日単位 bootstrap 95%CI）──")
    print("  {:32s} {:>24s} {:>24s} {:>24s}".format("腕", "Δ10万+/日", "Δ表示的中pt", "Δ30万+/日"))
    for nm, _s, _t, _p in ARMS[1:]:
        b = L.dboot(base, out[nm][0], days)
        print("  {:32s} {:>24s} {:>24s} {:>24s}".format(
            nm, L.ci(b["big"]), L.ci(b["shown"]), L.ci(b["b30"])))
