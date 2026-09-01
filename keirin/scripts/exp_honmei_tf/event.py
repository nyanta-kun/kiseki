#!/usr/bin/env python3
"""目標イベント E =（確定三連単50倍以上 ∧ 指数1位が3着以内）の解剖（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import PROB, PO, WIN, PAY, DATE, RTYPE, P3, PW, CANON, H, OK, EXP, CNF

N = len(WIN)
TOP3 = CANON[np.clip(WIN, 0, None)]                     # (N,3) 実1-3着の車番
RANKP3 = np.argsort(np.argsort(-P3, 1), 1) + 1          # 車番index→p3順位
FIN_R = RANKP3[np.arange(N)[:, None], TOP3 - 1]         # (N,3) 1-3着の指数順位
IN3 = (FIN_R == 1).any(1) & OK                          # 指数1位が3着以内
HI = (PAY >= 5000) & OK                                 # 確定50倍以上
E = IN3 & HI

print("=" * 100)
print("■ ベースレート（着順そろい 36,237R）")
print("=" * 100)
for wn, w in (("全期間", OK), ("探索", EXP), ("確認", CNF)):
    n = w.sum()
    print(f"{wn} n={n:,}  指数1位が3着内 {IN3[w].mean():.1%}  確定50倍+ {HI[w].mean():.1%}  "
          f"**両方 E** {E[w].mean():.1%}  （独立なら {IN3[w].mean()*HI[w].mean():.1%}）")
print(f"\n条件付き: P(50倍+ | 指数1位3着内) = {HI[IN3].mean():.1%}   "
      f"P(50倍+ | 指数1位が飛ぶ) = {HI[OK & ~IN3].mean():.1%}")
print(f"          P(指数1位3着内 | 50倍+) = {IN3[HI].mean():.1%}   "
      f"P(指数1位3着内 | 50倍未満) = {IN3[OK & ~HI].mean():.1%}")

print("\n" + "=" * 100)
print("■ E の内訳 — 指数1位はどこに入っているか / 配当はどれくらいか")
print("=" * 100)
pos = np.where(FIN_R == 1)[1] if False else None
p1pos = np.full(N, -1)
for j in range(3):
    p1pos[(FIN_R[:, j] == 1)] = j + 1
for j, nm in ((1, "1着"), (2, "2着"), (3, "3着")):
    m = E & (p1pos == j)
    print(f"指数1位が{nm}: E の {m.sum()/E.sum():5.1%}  n={m.sum():,}  "
          f"配当中央 {np.median(PAY[m]):>7,.0f}円  100倍+ {np.mean(PAY[m]>=10000):5.1%}")
m = OK & ~IN3 & HI
print(f"参考・指数1位が飛んだ50倍+: n={m.sum():,}  配当中央 {np.median(PAY[m]):>7,.0f}円")

print("\n" + "=" * 100)
print("■ E レースの決着パターン（1-3着の指数順位の組・順序つき）上位20")
print("=" * 100)
key = FIN_R[:, 0] * 100 + FIN_R[:, 1] * 10 + FIN_R[:, 2]
uk, cnt = np.unique(key[E], return_counts=True)
o = np.argsort(-cnt)
cum = 0
print(f"{'順位の組':<12}{'件数':>7}{'割合':>8}{'累積':>8}   配当中央")
for i in o[:20]:
    k = uk[i]; m = E & (key == k)
    cum += cnt[i] / E.sum()
    print(f"{k//100}-{k//10%10}-{k%10:<8}{cnt[i]:>7,}{cnt[i]/E.sum():>7.1%}{cum:>8.1%}"
          f"   {np.median(PAY[m]):>8,.0f}円")
print(f"\nE の決着パターン総数 {len(uk)}種 / 指数1位を含む順列は 90種")

print("\n" + "=" * 100)
print("■ 相手（2・3着）に来る指数順位の分布 — E レース vs 50倍未満で指数1位3着内")
print("=" * 100)
others = np.where(FIN_R == 1, 99, FIN_R)                 # 指数1位を除いた2車の順位
for lbl, m in (("E（50倍+）", E), ("50倍未満・指数1位3着内", IN3 & ~HI)):
    v = others[m]; v = v[v < 99]
    hist = np.bincount(v, minlength=8)[1:8]
    print(f"{lbl:<24} " + "  ".join(f"{r}位 {h/hist.sum():5.1%}" for r, h in enumerate(hist, 1)))
