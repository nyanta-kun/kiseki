#!/usr/bin/env python3
"""混戦度選別の窓別安定性 + 決勝系との組み合わせ（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import (PROB, PO, WIN, PAY, DATE, RTYPE, OK, EXP, CNF, POSMASK, BUDGET, UNIT)
from frame import buy, CONC, KESSHO

# 四半期窓
Q = np.array([f"{d[:4]}Q{(int(d[5:7])-1)//3+1}" for d in DATE])
QS = sorted(set(Q[OK]))

def line(label, mask_sel, lo, k):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k)
    cells = []
    for q in QS:
        m = mask_sel & (Q == q) & OK & (n > 0)
        if m.sum() < 50:
            cells.append(f"{q}:  n/a"); continue
        cells.append(f"{q}:{ret[m].sum()/inv[m].sum()*100:6.1f}%")
    m = mask_sel & OK & (n > 0)
    days = len(set(DATE[m]))
    # ブートストラップ CI（レース単位）
    rng = np.random.default_rng(0)
    idx = np.flatnonzero(m)
    bs = [ret[s].sum() / inv[s].sum() * 100
          for s in (rng.choice(idx, len(idx)) for _ in range(400))]
    lo_ci, hi_ci = np.percentile(bs, [2.5, 97.5])
    print(f"{label:<26} {m.sum()/days:5.2f}件/日 通算{ret[m].sum()/inv[m].sum()*100:6.1f}% "
          f"CI[{lo_ci:5.1f},{hi_ci:5.1f}]  " + " ".join(cells))

print("=" * 190)
print("■ 四半期別 ROI（壁 74.85%）— 50倍+ 確率順5点")
print("=" * 190)
line("全レース", np.ones(len(WIN), bool), 50, 5)
for q in (5, 10, 20, 30):
    line(f"混戦 下位{q}%", CONC <= np.percentile(CONC[OK], q), 50, 5)
line("決勝系", KESSHO, 50, 5)
line("決勝系 or 混戦下位10%", KESSHO | (CONC <= np.percentile(CONC[OK], 10)), 50, 5)
line("決勝系 かつ 混戦下位50%", KESSHO & (CONC <= np.percentile(CONC[OK], 50)), 50, 5)

print("\n" + "=" * 190)
print("■ 同じ選別を 30倍+ でやると（比較）")
print("=" * 190)
line("全レース", np.ones(len(WIN), bool), 30, 5)
for q in (10, 20):
    line(f"混戦 下位{q}%", CONC <= np.percentile(CONC[OK], q), 30, 5)
line("決勝系", KESSHO, 30, 5)

print("\n" + "=" * 190)
print("■ 混戦度の絶対閾値（上位5点確率和）— 運用で使うなら絶対値で切る")
print("=" * 190)
for q in (5, 10, 20, 30, 50):
    print(f"  下位{q}% ⇔ 上位5点確率和 <= {np.percentile(CONC[OK], q):.4f}")
print(f"  参考: 全体の中央 {np.median(CONC[OK]):.4f} / 探索窓中央 {np.median(CONC[EXP]):.4f} "
      f"/ 確認窓中央 {np.median(CONC[CNF]):.4f}")
