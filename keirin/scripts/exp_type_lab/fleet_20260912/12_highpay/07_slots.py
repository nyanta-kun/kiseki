#!/usr/bin/env python3
"""枠の本数 × 商品 の掃引。**層で絞るより「枠を少なくする」方が良くないか**の検算。

🔴 層（低信頼層）は在庫を絞るので枠が埋まらない。同じ本数なら
   「枠を少なくして優先度の高い在庫から埋める」腕と比べないと層の価値は見えない。
   交換レート = Δ10万+/日 ÷ Δ表示的中pt（`ceiling_and_instrumentation` の既存3機構は 0.030/pt）。
"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

ALL6 = tuple("ABCDEF"); BCD = ("B", "C", "D")
LAM = float(sys.argv[1]) if len(sys.argv) > 1 else 0.50

for win in ("explore", "confirm"):
    nd = L.ndays(win)
    base = L.simulate(win, hp_slots=5, hp_types=BCD, supply=("cap",), frac=LAM)
    kb = L.kpi(base, nd)
    print(f"\n{'='*126}\n[{win}] λ={LAM}  {nd}日   基準①: 表示的中 {kb['shown']:.2f}% / "
          f"10万+ {kb['big']:.3f}/日 / 30万+ {kb['b30']:.3f}\n{'='*126}")
    print("  {:26s} {:>5s} {:>6s} {:>8s} {:>8s} {:>8s} {:>9s} {:>6s} {:>8s}".format(
        "腕（全6型・cap+axis）", "本数", "枠/日", "表示的中", "10万+/日", "30万+/日",
        "払戻中央", "ROI", "交換/pt"))
    for product in ("alt", "sign", "bust15", "big"):
        for slots in (0, 2, 3, 5, 7, 10, 13, 99):
            if slots == 0 and product != "alt":
                continue
            r = L.simulate(win, hp_slots=slots, hp_types=ALL6,
                           supply=("cap", "axis"), frac=LAM, product=product)
            k = L.kpi(r, nd)
            c = Counter(x["origin"] for x in r); f = (c["hp:cap"] + c["hp:axis"]) / nd
            dsh = kb["shown"] - k["shown"]; dbig = k["big"] - kb["big"]
            ex = dbig / dsh if dsh > 0.01 else float("nan")
            nm = ("高額枠なし" if slots == 0 else f"{product} {slots}本")
            print(f"  {nm:26s} {slots:5d} {f:6.2f} {k['shown']:8.2f} {k['big']:8.3f}"
                  f" {k['b30']:8.3f} {k['med_pay']:9,.0f} {k['roi']:6.1f} {ex:8.3f}")
    # 層を掛けた場合の交換レート（同じ本数の非層腕と並べる）
    print("\n  ── 層 vs 枠を少なくする（同じ枠/日で比べる）──")
    print("  {:30s} {:>6s} {:>8s} {:>8s} {:>8s}".format("腕", "枠/日", "表示的中", "10万+/日", "交換/pt"))
    rows = []
    for layer in ("pw_gap12_lo25", "axis_sum_lo25", "p3_gap12_lo25"):
        r = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                       frac=LAM, product="bust15", layer=layer)
        k = L.kpi(r, nd); c = Counter(x["origin"] for x in r)
        rows.append((f"層={layer} 10本", (c["hp:cap"] + c["hp:axis"]) / nd, k))
    for slots in (4, 5, 6, 7, 8, 9):
        r = L.simulate(win, hp_slots=slots, hp_types=ALL6, supply=("cap", "axis"),
                       frac=LAM, product="bust15")
        k = L.kpi(r, nd); c = Counter(x["origin"] for x in r)
        rows.append((f"層なし {slots}本", (c["hp:cap"] + c["hp:axis"]) / nd, k))
    for nm, f, k in sorted(rows, key=lambda x: x[1]):
        dsh = kb["shown"] - k["shown"]; dbig = k["big"] - kb["big"]
        print(f"  {nm:30s} {f:6.2f} {k['shown']:8.2f} {k['big']:8.3f}"
              f" {dbig/dsh if dsh>0.01 else float('nan'):8.3f}")
