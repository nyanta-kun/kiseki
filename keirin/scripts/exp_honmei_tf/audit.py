#!/usr/bin/env python3
"""本命条件の実効性と「高配当が出るレース」の予測可能性（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import (PROB, PO, WIN, PAY, DATE, RTYPE, P3, PW, CANON, H, OK, EXP, CNF,
                  POSMASK, evaluate)

print("=" * 100)
print("■ 1. 制約なしで確率上位5点を買うと、その中に本命は何点入るか")
print("=" * 100)
for lo in (1, 30, 50, 100):
    sc = np.where(PO >= lo, PROB, -np.inf)
    top = np.argsort(-sc, axis=1)[:, :5]
    hasH = (CANON[top][:, :, :] == H[:, None, None]).any(2)     # (N,5)
    print(f"{lo:>4}倍+ 上位5点: 本命を含む点数 平均{hasH.sum(1).mean():.2f}/5点 "
          f"（5点とも本命入り {(hasH.sum(1)==5).mean():.1%} / 1点も無い {(hasH.sum(1)==0).mean():.1%}）")

print("\n" + "=" * 100)
print("■ 2. 的中の上限 — 本命が実際に3着以内だったレースに限ると的中率はどうなるか")
print("=" * 100)
TOP3 = CANON[np.clip(WIN, 0, None)]
in3 = (TOP3 == H[:, None]).any(1) & OK
for lo, k in ((30, 5), (50, 5), (50, 10)):
    n, inv, ret, hit = evaluate(POSMASK["any"] & (PO >= lo), PROB, k)
    for wn, w in (("探索", EXP), ("確認", CNF)):
        m = w & (n > 0)
        a = m & in3
        print(f"{lo}倍+{k}点 {wn}: 全体的中{hit[m].mean()*100:5.2f}%  "
              f"本命3着内だった{a.sum()/m.sum():5.1%}のレースに限ると{hit[a].mean()*100:5.2f}%  "
              f"本命が飛んだ残り{(m&~in3).sum()/m.sum():5.1%}は的中0%（構造上）")

print("\n" + "=" * 100)
print("■ 3. 実際の三連単配当の分布（1点100円あたり）")
print("=" * 100)
for wn, w in (("探索", EXP), ("確認", CNF)):
    p = PAY[w]
    print(f"{wn} n={w.sum():,}  中央{np.median(p):,.0f}円  "
          + "  ".join(f"{t/100:.0f}倍+ {np.mean(p>=t):.1%}" for t in (3000, 5000, 10000, 30000, 100000)))

print("\n" + "=" * 100)
print("■ 4. 「配当50倍(5,000円)以上が出るレース」は事前に分かるか（単一量のAUC）")
print("=" * 100)
def auc(score, y):
    o = np.argsort(score); y = y[o]
    r = np.arange(1, len(y) + 1)
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0: return np.nan
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)

p3n = P3 / P3.sum(1, keepdims=True)
pwn = PW / PW.sum(1, keepdims=True)
feats = {
    "本命p3（低いほど荒れる想定）": -P3.max(1),
    "本命pw（1着率トップ）": -PW.max(1),
    "1着率エントロピー": -(pwn * np.log(pwn + 1e-12)).sum(1),
    "抜け度 gap12（pw 1位-2位）": -np.diff(np.sort(pwn, 1)[:, ::-1][:, :2], axis=1).ravel(),
    "予測板の100倍+点数": (PO >= 100).sum(1).astype(float),
    "予測板の上位5点確率和": np.sort(PROB, 1)[:, ::-1][:, :5].sum(1) * -1,
}
for thr, lbl in ((5000, "50倍+"), (10000, "100倍+"), (30000, "300倍+")):
    print(f"--- 目標: 確定配当 {lbl} ---")
    for nm, f in feats.items():
        a_e = auc(f[EXP], (PAY >= thr)[EXP]); a_c = auc(f[CNF], (PAY >= thr)[CNF])
        print(f"   {nm:<28} 探索 {a_e:.4f}  確認 {a_c:.4f}")
