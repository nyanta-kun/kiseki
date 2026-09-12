#!/usr/bin/env python3
"""推奨形（10本・B/C/D・cap+axis・商品は現行 alt）を λ 掃引で。前向き判定の Poisson も。"""
from __future__ import annotations
import sys
from collections import Counter
from math import exp, log, lgamma
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

BCD = ("B", "C", "D"); ALL6 = tuple("ABCDEF")
print("=== 推奨形の λ 感度（基準①: 5本/BCD/cap・現行商品 alt）===")
print("  {:6s} {:8s} {:>7s} {:>8s} {:>9s} {:>9s} {:>9s} {:>10s} {:>10s}".format(
    "λ", "窓", "枠/日", "充填%", "件/日", "表示的中", "10万+/日", "Δ10万+", "Δ表示的中"))
for lam in (0.50, 0.40, 0.35, 0.30, 0.25):
    for win in ("explore", "confirm"):
        nd = L.ndays(win); days = sorted({r["date"] for r in L.load() if r["win"] == win})
        b = L.simulate(win, hp_slots=5, hp_types=BCD, supply=("cap",), frac=lam)
        a = L.simulate(win, hp_slots=10, hp_types=BCD, supply=("cap", "axis"), frac=lam)
        c = Counter(x["origin"] for x in a); f = (c["hp:cap"] + c["hp:axis"]) / nd
        ka, kb = L.kpi(a, nd), L.kpi(b, nd); d = L.dboot(b, a, days)
        print(f"  {lam:6.2f} {win:8s} {f:7.2f} {f/10*100:8.1f} {ka['perday']:9.2f}"
              f" {ka['shown']:9.2f} {ka['big']:9.3f} {L.ci(d['big']):>10s} {L.ci(d['shown']):>10s}")

def pois_ge(k, lam):
    return 1 - sum(exp(-lam + i * log(lam) - lgamma(i + 1)) for i in range(k))

print("\n=== 前向き判定（計画 §9「30日で 10万+ が12件以上なら継続」）を実測の基準値で評価 ===")
print("  🔴 §9 の『現行 0.213件/日＝月6.4件』は **高額枠を含まない** 台の値（上限も未適用）。")
print("     実入稿（2026-08-29〜09-12・15日・型ラボ）は **10万+ 6件 = 0.40件/日** で、")
print("     高額枠は既に 4〜5本/日 出ている。")
for nm, l0, dl in (("§9 の前提", 0.213, 0.600 - 0.213),
                   ("台 λ=0.35 の実測Δ", 0.282, 0.204),
                   ("台 λ=0.30 の実測Δ", 0.259, 0.171),
                   ("実入稿の基準 0.40 + Δ0.20", 0.400, 0.204),
                   ("実入稿の基準 0.40 + Δ0.17", 0.400, 0.171)):
    a, bb = l0 * 30, (l0 + dl) * 30
    print(f"  {nm:26s} 現行 {a:5.1f}件/30日 → 腕 {bb:5.1f}件/30日  "
          f"P(>=12|現行)={pois_ge(12, a)*100:5.1f}%  P(>=12|腕)={pois_ge(12, bb)*100:5.1f}%  "
          f"P(<=6|腕)={(1-pois_ge(7, bb))*100:4.1f}%")
