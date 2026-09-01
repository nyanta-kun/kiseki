#!/usr/bin/env python3
"""◎軸フォーメーション vs 確率上位N点（2026-08-26）。"""
from __future__ import annotations
import itertools, sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import (PROB, PO, WIN, PAY, DATE, RTYPE, P3, CANON, H, OK, EXP, CNF,
                  POSMASK, BUDGET, UNIT)
from frame import buy, rep, CONC, KESSHO

N = len(WIN)
ALL = np.ones(N, bool)
# p3 の順位（1位=本命）
RANK = np.argsort(np.argsort(-P3, 1), 1) + 1          # (N,7) 車番index→順位
CRANK = RANK[np.arange(N)[:, None, None], CANON[None] - 1]   # (N,210,3) 各目の3車の順位

def box_mask(k):
    """◎ + p3 上位2..k+1 位の (k+1) 車ボックスのうち、◎を含む目。"""
    return POSMASK["any"] & (CRANK <= k + 1).all(2)

print("=" * 158)
print("■ ◎軸ボックス（◎＋p3上位k車）— 50倍+ フィルタあり / 確率順で点数を切る")
print("=" * 158)
for k in (2, 3, 4):
    m = box_mask(k)
    print(f"  相手{k}車ボックス: ◎含む目 {m[0].sum()}点 / うち50倍+ {(m&(PO>=50)).sum(1).mean():.1f}点（平均）")
for k, nlist in ((2, (6,)), (3, (6, 10, 18)), (4, (6, 10))):
    for nl in nlist:
        n, inv, ret, hit = buy(box_mask(k) & (PO >= 50), PROB, nl)
        rep(f"◎+{k}車ボックス 50倍+ {nl}点", ALL, n, inv, ret, hit)
        n, inv, ret, hit = buy(box_mask(k) & (PO >= 50), PROB, nl)
        rep(f"  └ 決勝系のみ", KESSHO, n, inv, ret, hit)

print("\n" + "=" * 158)
print("■ 同点数での直接対決（全レース・50倍+）")
print("=" * 158)
for nl in (5, 6, 10):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= 50), PROB, nl)
    rep(f"確率上位{nl}点（制約なし）", ALL, n, inv, ret, hit)
    n, inv, ret, hit = buy(box_mask(3) & (PO >= 50), PROB, nl)
    rep(f"◎+3車ボックス内 上位{nl}点", ALL, n, inv, ret, hit)

print("\n" + "=" * 158)
print("■ 本命の位置を固定したフォーメーション（相手 = p3 上位2..5位の4車・50倍+）")
print("=" * 158)
P4 = (CRANK <= 5).all(2)          # 3車とも p3 上位5位以内
for pos, nm in ((1, "◎-相手-相手"), (2, "相手-◎-相手"), (3, "相手-相手-◎")):
    for nl in (5, 8):
        n, inv, ret, hit = buy(POSMASK[pos] & P4 & (PO >= 50), PROB, nl)
        rep(f"{nm} {nl}点", ALL, n, inv, ret, hit)
