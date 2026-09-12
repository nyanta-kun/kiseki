#!/usr/bin/env python3
"""交換レート Δ10万+/日 ÷ Δ表示的中pt。既存3機構は 0.030/pt（ceiling_and_instrumentation）。
🔴 計画 §1 は 0.083/pt（＝既存の2.8倍）を採用検討の理由にしている。制約込みでどうなるか。"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L
BCD = ("B", "C", "D"); ALL6 = tuple("ABCDEF")
print("  {:44s} {:>9s} {:>9s} {:>9s} {:>9s}".format("腕", "Δ表示的中", "Δ10万+", "交換/pt", "既存比"))
for win in ("explore", "confirm"):
    nd = L.ndays(win)
    print(f"[{win}]")
    b = L.kpi(L.simulate(win, hp_slots=5, hp_types=BCD, supply=("cap",), frac=0.50), nd)
    b0 = L.kpi(L.simulate(win, hp_slots=0, frac=0.0), nd)
    arms = [
        ("計画 §1: 上限なし・軸ゲート落ち全量 bust15", dict(hp_slots=99, hp_types=ALL6,
            supply=("axis",), frac=0.0, product="bust15"), b0),
        ("⑥b λ=0.50 10本/BCD/cap+axis/alt", dict(hp_slots=10, hp_types=BCD,
            supply=("cap", "axis"), frac=0.50), b),
        ("⑥b λ=0.35 10本/BCD/cap+axis/alt", dict(hp_slots=10, hp_types=BCD,
            supply=("cap", "axis"), frac=0.35), None),
        ("⑥b' λ=0.50 10本/全6型/cap+axis/alt", dict(hp_slots=10, hp_types=ALL6,
            supply=("cap", "axis"), frac=0.50), b),
        ("⑥b λ=0.50 10本/BCD/cap+axis/sign", dict(hp_slots=10, hp_types=BCD,
            supply=("cap", "axis"), frac=0.50, product="sign"), b),
        ("⑥b' λ=0.50 10本/全6型/cap+axis/sign", dict(hp_slots=10, hp_types=ALL6,
            supply=("cap", "axis"), frac=0.50, product="sign"), b),
    ]
    for nm, kw, bas in arms:
        if bas is None:
            bas = L.kpi(L.simulate(win, hp_slots=5, hp_types=BCD, supply=("cap",),
                                   frac=kw["frac"]), nd)
        k = L.kpi(L.simulate(win, **kw), nd)
        ds = bas["shown"] - k["shown"]; db = k["big"] - bas["big"]
        print(f"  {nm:44s} {-ds:9.2f} {db:+9.3f} {db/ds:9.3f} {db/ds/0.030:8.2f}x")
