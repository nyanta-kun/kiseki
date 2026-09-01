#!/usr/bin/env python3
"""本命絡み三連単の基礎統計（2026-08-26）。"""
from __future__ import annotations
import itertools, sys
import numpy as np

z = np.load("/tmp/honmei_tf.npz", allow_pickle=True)
PROB, PO, WIN, PAY = z["PROB"], z["PO"], z["WIN"], z["PAY"]
DATE, RTYPE, P3, PW = z["DATE"], z["RTYPE"], z["P3"], z["PW"]
N = len(WIN)
CANON = np.array(list(itertools.permutations(range(1, 8), 3)))   # (210,3)

sel = WIN >= 0
print(f"全レース {N:,} / 着順そろい {sel.sum():,}  ({sorted(DATE)[0]}〜{sorted(DATE)[-1]})")

# 本命の定義2種
h_p3 = P3.argmax(1) + 1
h_pw = PW.argmax(1) + 1
print(f"◎(p3 1位) と 勝率1位 の一致率: {(h_p3==h_pw).mean():.1%}")

TOP3 = CANON[np.clip(WIN, 0, None)]              # (N,3) 実際の1-3着
def in3(h): return (TOP3 == h[:, None]).any(1) & sel
def is1(h): return (TOP3[:, 0] == h) & sel

for nm, h in (("◎=p3 1位", h_p3), ("勝率1位", h_pw)):
    print(f"{nm}: 3着内率 {in3(h).sum()/sel.sum():.1%}  1着率 {is1(h).sum()/sel.sum():.1%}")

# 本命を含む買い目マスク (N,210)
h = h_p3
HAS = (CANON[None, :, :] == h[:, None, None]).any(2)     # (N,210)
print(f"\n本命を含む目の点数: {HAS.sum(1)[0]}点 / 210点")

# 予測オッズ帯ごとの点数（本命を含む目の中で）
print("\n■ 本命を含む90点のうち、予測オッズが下限以上の点数")
print(f"{'下限':>6} {'平均点数':>9} {'中央':>6} {'1点以上あるR':>13} {'5点以上あるR':>13}")
for lo in (10, 20, 30, 50, 75, 100, 150, 300):
    n = (HAS & (PO >= lo)).sum(1)
    print(f"{lo:>5}倍 {n.mean():>9.1f} {np.median(n):>6.0f} "
          f"{(n>=1).mean():>12.1%} {(n>=5).mean():>12.1%}")

# 50倍以上が発生するレースの性質
m50 = (HAS & (PO >= 50)).sum(1)
for lo, nm in ((50, "50倍+"),):
    for cond, lbl in ((m50 >= 1, f"{nm}が1点以上"), (m50 == 0, f"{nm}が0点")):
        c = cond & sel
        print(f"\n{lbl}: {c.sum():,}R ({c.sum()/sel.sum():.1%})  "
              f"本命3着内 {in3(h)[c].mean():.1%}  本命1着 {is1(h)[c].mean():.1%}  "
              f"実払戻中央 {np.median(PAY[c]):,.0f}円")
