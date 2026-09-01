#!/usr/bin/env python3
"""E レースの中身 — 何点買えばどれだけ取れるか / 選別を買いに変換できるか（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import PROB, PO, WIN, PAY, DATE, RTYPE, P3, PW, CANON, H, OK, EXP, CNF, POSMASK
from frame import buy

N = len(WIN)
TOP3 = CANON[np.clip(WIN, 0, None)]
RANKP3 = np.argsort(np.argsort(-P3, 1), 1) + 1
FIN_R = RANKP3[np.arange(N)[:, None], TOP3 - 1]
IN3 = (FIN_R == 1).any(1) & OK
HI = (PAY >= 5000) & OK
E = IN3 & HI
CONC = np.sort(PROB, 1)[:, ::-1][:, :5].sum(1)

print("=" * 116)
print("■ 1. E レースだけを買えたら（オラクル上限）— 確率上位N点・均等・1万円")
print("=" * 116)
print(f"{'点数':>5}{'50倍+帯 的中':>14}{'ROI':>9}{'中央':>11}   {'制限なし帯 的中':>15}{'ROI':>9}{'中央':>11}")
for k in (3, 5, 8, 12, 20, 30):
    row = f"{k:>5}"
    for lo in (50, 1):
        n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k)
        m = E & (n > 0)
        row += (f"{hit[m].mean()*100:>13.1f}%{ret[m].sum()/inv[m].sum()*100:>8.1f}%"
                f"{np.median(ret[m][hit[m]]):>10,.0f}円")
    print(row)

print("\n" + "=" * 116)
print("■ 2. E レースでの取りこぼし — 50倍+5点で外す理由（確認窓）")
print("=" * 116)
n, inv, ret, hit = buy(POSMASK["any"] & (PO >= 50), PROB, 5)
m = E & CNF & (n > 0)
miss = m & ~hit
r2 = np.sort(np.where(FIN_R == 1, 99, FIN_R), 1)[:, :2]
print(f"E かつ確認窓 n={m.sum():,}  的中 {hit[m].mean():.1%}  外れ {miss.sum():,}")
print("  外したレースの相手2車の指数順位（上位10組）:")
kk = r2[:, 0] * 10 + r2[:, 1]
u, c = np.unique(kk[miss], return_counts=True)
for i in np.argsort(-c)[:10]:
    print(f"    {u[i]//10}位+{u[i]%10}位 : {c[i]:>4}件 ({c[i]/miss.sum():5.1%})  "
          f"配当中央 {np.median(PAY[miss & (kk==u[i])]):>8,.0f}円")
print("  的中したレースの相手2車:")
u, c = np.unique(kk[m & hit], return_counts=True)
for i in np.argsort(-c)[:5]:
    print(f"    {u[i]//10}位+{u[i]%10}位 : {c[i]:>4}件 ({c[i]/(m&hit).sum():5.1%})")

print("\n" + "=" * 116)
print("■ 3. E 選別（混戦度 CONC）を買いに変換すると — 帯ごとに符号が変わる")
print("=" * 116)
for lo, k in ((50, 5), (50, 10), (30, 5), (100, 5)):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k)
    cells = []
    for wn, w in (("探索", EXP), ("確認", CNF)):
        base = w & (n > 0)
        th = np.percentile(CONC[base], 10)
        s = base & (CONC <= th)
        cells.append(f"{wn}: 全{ret[base].sum()/inv[base].sum()*100:5.1f}% → "
                     f"混戦上位10%{ret[s].sum()/inv[s].sum()*100:5.1f}% "
                     f"(E率 {E[base].mean():4.1%}→{E[s].mean():4.1%})")
    print(f"{lo:>3}倍+ {k:>2}点   " + "   ".join(cells))
