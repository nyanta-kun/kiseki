#!/usr/bin/env python3
"""10万+ を「荒れる確率 × 荒れたときの配当」へ分解する（2026-09-04）。

単一量が全滅（AUC 0.45〜0.55・両窓で符号反転）した理由の検証。
仮説: 混戦レースほど荒れるが1点あたりの配当は安く、堅いレースほど荒れないが
      荒れたときは高い。積が一定なら基準率は動かない。
"""
from __future__ import annotations
import itertools, json
import numpy as np

CANON = list(itertools.permutations(range(1, 8), 3))
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
DNF = set(json.load(open("/tmp/dnf_races.json")))
KEY, DATE, PAY, WIN = z["KEY"], z["DATE"], z["PAY"], z["WIN"]
P3, PW = z["P3"].astype(float), z["PW"].astype(float)
PROB, PO = z["PROB"].astype(float), z["PO"].astype(float)
M = ((z["TYPE"] != "") & (WIN >= 0) & np.isfinite(PAY) & z["OKPRED"]
     & np.array([str(k) not in DNF for k in KEY]))
BIG = PAY >= 100_000
W = {"探索": (DATE >= "2024-07-01") & (DATE <= "2025-12-31"), "確認": (DATE >= "2026-01-01")}

# 決着の中身: 1〜3着車が「複勝率(P3)の何番手だったか」
rank_of = np.argsort(np.argsort(-P3, axis=1), axis=1) + 1     # 車番index -> 順位
top3_ranks = np.full((len(KEY), 3), -1, int)
for i in np.flatnonzero(M):
    for j, car in enumerate(CANON[WIN[i]]):
        top3_ranks[i, j] = rank_of[i, car - 1]
axis1_out = top3_ranks.max(axis=1) * 0 + (top3_ranks != 1).all(axis=1)   # 指数1位が3着外
worst = top3_ranks.max(axis=1)

print("=" * 92)
print("■ 10万+ レースの決着の中身（DNF なし）")
print("=" * 92)
for wn, wm in W.items():
    m = M & wm
    for lab, s in [("全体", m), ("10万+", m & BIG), ("1万未満", m & (PAY < 10_000))]:
        r = top3_ranks[s]
        print(f"  {wn} {lab:8s} n={s.sum():6,}  "
              f"指数1位が3着外 {(r != 1).all(axis=1).mean()*100:5.1f}%  "
              f"3着内の指数順位 平均 {r.mean():4.2f}  最下位の平均 {r.max(axis=1).mean():4.2f}  "
              f"5番手以下を含む {(r >= 5).any(axis=1).mean()*100:5.1f}%")
    print()

print("=" * 92)
print("■ 分解: 荒れる確率 × 荒れたときに10万+ になる確率")
print("   （層 = 上位2車の複勝率和 axis_sum の五分位 ＝ 堅さ）")
print("=" * 92)
AX = z["AXIS_SUM"].astype(float)
for wn, wm in W.items():
    m = M & wm
    qs = np.quantile(AX[m], [0.2, 0.4, 0.6, 0.8])
    b = np.digitize(AX[m], qs)
    print(f"\n  {wn}窓")
    print(f"    {'axis_sum':16s} {'n':>7s} {'指数1位が3着外':>13s} {'その中で10万+':>13s} "
          f"{'10万+全体':>10s} {'払戻中央(飛んだ時)':>18s}")
    for k in range(5):
        s = b == k
        idx = np.flatnonzero(m)[s]
        out = (top3_ranks[idx] != 1).all(axis=1)
        big = BIG[idx]
        med = np.median(PAY[idx][out]) if out.sum() else float("nan")
        print(f"    Q{k+1} [{AX[idx].min():.2f},{AX[idx].max():.2f}] {s.sum():7,} "
              f"{out.mean()*100:12.1f}% {big[out].mean()*100:12.1f}% "
              f"{big.mean()*100:9.2f}% {med:17,.0f}")

print()
print("=" * 92)
print("■ モデル自身の予測（Σ 予測オッズ1000倍+ の目の確率）で当てられるか")
print("=" * 92)
s_model = np.where(PO >= 1000, PROB, 0.0).sum(axis=1)          # モデルの 10万+ 確率
s_mkt = np.where(PO >= 1000, np.where(PO > 0, 1 / PO, 0), 0).sum(axis=1)  # 板のシェア

def auc(sc, y):
    r = np.argsort(np.argsort(sc)) + 1.0
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")

for name, v in [("モデル確率 Σp(1000倍+)", s_model), ("板シェア Σ1/PO(1000倍+)", s_mkt)]:
    print(f"\n  {name}")
    for wn, wm in W.items():
        m = M & wm
        a = auc(v[m], BIG[m])
        qs = np.quantile(v[m], [0.2, 0.4, 0.6, 0.8])
        b = np.digitize(v[m], qs)
        cells = " ".join(f"{BIG[m][b == k].mean()*100:5.2f}%" for k in range(5))
        pred = " ".join(f"{v[m][b == k].mean()*100:5.2f}%" for k in range(5))
        print(f"    {wn} AUC {a:.3f}  実測 低→高 {cells}")
        print(f"    {'':4s}           予測 低→高 {pred}")
