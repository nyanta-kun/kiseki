#!/usr/bin/env python3
"""本命絡み三連単: レース選別の掃引（2026-08-26）。"""
from __future__ import annotations
import itertools, sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import (PROB, PO, WIN, PAY, DATE, RTYPE, P3, CANON, H, OK, EXP, CNF,
                  POSMASK, EV, evaluate, BUDGET, UNIT)

def rep(label, sub, n, inv, ret, hit, quiet=False):
    cells = []
    for wn, w in (("探索", EXP), ("確認", CNF)):
        m = sub & w & (n > 0)
        if m.sum() < 30:
            cells.append(f"{wn}: n={m.sum()}"); continue
        days = len(set(DATE[m]))
        roi = ret[m].sum() / inv[m].sum() * 100
        med = np.median(ret[m][hit[m]]) if hit[m].any() else 0
        cells.append(f"{wn}: {m.sum()/days:5.2f}件/日 的中{hit[m].mean()*100:5.2f}% "
                     f"ROI{roi:6.1f}% 中央{med:8,.0f}円 10万+{((ret>=100000)&m).sum():4d}件")
    print(f"{label:<40} " + "  ".join(cells))

ALL = np.ones(len(WIN), bool)
p3h = P3.max(1)                      # 本命の3着内率（モデル）
KESSHO = np.isin(RTYPE, ["決勝", "チャレンジ決勝"])

print("=" * 152)
print("■ レース選別（本命どこでも・確率順5点）")
print("=" * 152)
for lo in (30, 50):
    print(f"--- 予測オッズ {lo}倍+ ---")
    n, inv, ret, hit = evaluate(POSMASK["any"] & (PO >= lo), PROB, 5)
    rep(f"{lo}倍+ 全レース", ALL, n, inv, ret, hit)
    rep(f"{lo}倍+ 決勝系のみ", KESSHO, n, inv, ret, hit)
    for lbl, q in (("上位25%", 75), ("上位50%", 50), ("下位50%", -50), ("下位25%", -25)):
        th = np.percentile(p3h[OK], abs(q))
        sub = (p3h >= th) if q > 0 else (p3h <= th)
        rep(f"{lo}倍+ 本命p3 {lbl}", sub, n, inv, ret, hit)

print("\n" + "=" * 152)
print("■ 点数を増やす（本命どこでも・確率順・全レース）")
print("=" * 152)
for lo in (30, 50):
    for k in (5, 8, 10, 12, 15):
        n, inv, ret, hit = evaluate(POSMASK["any"] & (PO >= lo), PROB, k)
        rep(f"{lo}倍+ {k}点", ALL, n, inv, ret, hit)

print("\n" + "=" * 152)
print("■ 決勝系 × 点数（7T3 との比較台）")
print("=" * 152)
for lo in (30, 50):
    for k in (5, 8, 10):
        n, inv, ret, hit = evaluate(POSMASK["any"] & (PO >= lo), PROB, k)
        rep(f"決勝系 {lo}倍+ {k}点 本命込み", KESSHO, n, inv, ret, hit)
    n, inv, ret, hit = evaluate(PO >= lo, PROB, 5)       # 本命条件なし = 7T3 相当
    rep(f"決勝系 {lo}倍+ 5点 【本命条件なし=7T3】", KESSHO, n, inv, ret, hit)
