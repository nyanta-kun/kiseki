#!/usr/bin/env python3
"""採否に直結する2つの対比較を CI 付きで。
  (a) 型F を足す価値 — 基準 ①+②（10本/BCD/cap+axis）
  (b) 商品の選択    — 基準 alt（本番の sign/big 交互）"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

ALL6 = tuple("ABCDEF"); BCD = ("B", "C", "D"); BCDF = ("B", "C", "D", "F")

for lam in (0.50, 0.35):
    for win in ("explore", "confirm"):
        nd = L.ndays(win); days = sorted({r["date"] for r in L.load() if r["win"] == win})
        print(f"\n[λ={lam} {win}]")
        # (a) 型を広げる価値（商品は bust15 と alt の両方で）
        for prod in ("alt", "bust15"):
            b = L.simulate(win, hp_slots=10, hp_types=BCD, supply=("cap", "axis"),
                           frac=lam, product=prod)
            for nm, tp in (("+型F (BCDF)", BCDF), ("+全6型", ALL6)):
                a = L.simulate(win, hp_slots=10, hp_types=tp, supply=("cap", "axis"),
                               frac=lam, product=prod)
                d = L.dboot(b, a, days)
                print(f"  (a) 商品={prod:7s} {nm:12s} Δ10万+ {L.ci(d['big']):>24s}"
                      f"  Δ表示的中 {L.ci(d['shown']):>22s}  Δ30万+ {L.ci(d['b30']):>22s}")
        # (b) 商品の選択（型は全6型）
        b = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                       frac=lam, product="alt")
        for prod in ("sign", "bust15", "big"):
            a = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                           frac=lam, product=prod)
            d = L.dboot(b, a, days)
            print(f"  (b) alt → {prod:7s}          Δ10万+ {L.ci(d['big']):>24s}"
                  f"  Δ表示的中 {L.ci(d['shown']):>22s}  Δ30万+ {L.ci(d['b30']):>22s}")
