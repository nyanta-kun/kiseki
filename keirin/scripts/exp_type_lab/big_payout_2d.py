#!/usr/bin/env python3
"""残った仮説: 「堅い(axis_sum 高) × 崩れの兆候」なら 10万+ を狙えるか（2026-09-04）。

分解で見えたのは
  混戦 = 飛ぶ確率33% × 飛んでも10万+は3.5%   （配当が安い）
  堅い = 飛ぶ確率 7% × 飛べば10万+が12%      （配当は高い）
なので、堅い層の中で「飛ぶ側」を選べれば積が上がるはず。層の中で残差の信号を探す。
"""
from __future__ import annotations
import itertools, json
import numpy as np

CANON = list(itertools.permutations(range(1, 8), 3))
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
DNF = set(json.load(open("/tmp/dnf_races.json")))
KEY, DATE, PAY, WIN = z["KEY"], z["DATE"], z["PAY"], z["WIN"]
P3, PW = z["P3"].astype(float), z["PW"].astype(float)
AX = z["AXIS_SUM"].astype(float)
M = ((z["TYPE"] != "") & (WIN >= 0) & np.isfinite(PAY) & z["OKPRED"]
     & np.array([str(k) not in DNF for k in KEY]))
BIG = PAY >= 100_000
W = {"探索": (DATE >= "2024-07-01") & (DATE <= "2025-12-31"), "確認": (DATE >= "2026-01-01")}

rank_of = np.argsort(np.argsort(-P3, axis=1), axis=1) + 1
top3 = np.full((len(KEY), 3), -1, int)
for i in np.flatnonzero(M):
    for j, car in enumerate(CANON[WIN[i]]):
        top3[i, j] = rank_of[i, car - 1]
BUST = (top3 != 1).all(axis=1)          # 指数1位が3着外

def ent(p):
    p = np.clip(p, 1e-9, None); p = p / p.sum()
    return float(-(p * np.log(p)).sum() / np.log(len(p)))
PWENT = np.array([ent(PW[i]) for i in range(len(PW))])
axis1 = np.argmax(P3, axis=1)
LSZ = z["A_line_size"].astype(float)[np.arange(len(KEY)), axis1]      # 軸1のライン人数
BEH = z["BEHIND"].astype(float)[np.arange(len(KEY)), axis1]           # 軸1の遅れ率
AGR = z["AGREE"]

print("=" * 100)
print("■ 堅さ(axis_sum 五分位) × 層内の崩れ兆候  →  10万+ の発生率")
print("=" * 100)
CONDS = [("軸1の遅れ率(高いほど自力なし)", BEH),
         ("単勝率の一様さ pw_ent", PWENT),
         ("軸1のライン人数", LSZ)]
for cname, cv in CONDS:
    print(f"\n  ── 層内3分割: {cname} ──")
    for wn, wm in W.items():
        m = M & wm
        idx = np.flatnonzero(m)
        qs = np.quantile(AX[idx], [0.2, 0.4, 0.6, 0.8])
        st = np.digitize(AX[idx], qs)
        print(f"    {wn}窓   " + "".join(f"{lab:>22s}" for lab in ["低", "中", "高"]))
        for k in range(5):
            sel = idx[st == k]
            cq = np.quantile(cv[sel], [1/3, 2/3])
            tb = np.digitize(cv[sel], cq)
            cells = []
            for t in range(3):
                s2 = sel[tb == t]
                cells.append(f"{BIG[s2].mean()*100:5.2f}%/飛{BUST[s2].mean()*100:4.1f}%({len(s2):4,})")
            print(f"      axis_sum Q{k+1} " + " ".join(cells))

print()
print("=" * 100)
print("■ 参考: 堅さ別の払戻の帯（DNF なし・全レース）")
print("=" * 100)
for wn, wm in W.items():
    m = M & wm
    idx = np.flatnonzero(m)
    qs = np.quantile(AX[idx], [0.2, 0.4, 0.6, 0.8])
    st = np.digitize(AX[idx], qs)
    print(f"\n  {wn}窓  {'':10s}{'中央':>9s}{'1万+':>8s}{'3万+':>8s}{'10万+':>8s}{'30万+':>8s}{'50万+':>8s}")
    for k in range(5):
        s = idx[st == k]
        p = PAY[s]
        print(f"    axis_sum Q{k+1} {np.median(p):9,.0f}"
              + "".join(f"{(p >= x).mean()*100:7.2f}%" for x in [10_000, 30_000, 100_000, 300_000, 500_000]))
