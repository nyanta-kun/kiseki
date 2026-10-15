#!/usr/bin/env python3
"""「3着内率が低い本命は飛ぶので次点を軸に」の前提の検算（2026-10-15）。

p3[軸1] の十分位ごとに、軸1/軸2/軸3 の予測3着内率と実3着内率を並べる。
軸1の実率 < 軸2の実率 になる帯があれば初めて「次点を軸に」が成り立つ。
台: /tmp/race_type_board.npz（7車）。結果: docs/type_lab/low_p3_axis_shift_2026_10_15.md
出力: /tmp/low_p3_axis/diag.out
"""
import itertools, os, sys
import numpy as np
os.makedirs("/tmp/low_p3_axis", exist_ok=True)
OUT = open("/tmp/low_p3_axis/diag.out", "w")
def p(*a):
    print(*a); print(*a, file=OUT); OUT.flush()
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
CANON = list(itertools.permutations(range(1, 8), 3))
P3 = z["P3"].astype(float); PW = z["PW"].astype(float)
WIN = z["WIN"].astype(int); DATE = z["DATE"].astype(str); AX = z["AXIS_SUM"].astype(float)
TYPE = z["TYPE"].astype(str); PAY = z["PAY"].astype(float)
M = (TYPE != "") & (WIN >= 0) & z["OKPRED"].astype(bool) & np.isfinite(P3).all(1)
N = len(WIN)
# order: p3 降順・同値は車番昇順（race_shape と同じ）
order = np.zeros((N, 7), int)
for i in np.flatnonzero(M):
    order[i] = sorted(range(1, 8), key=lambda c: (-P3[i][c-1], c))
top3 = np.zeros((N, 7), bool)
for i in np.flatnonzero(M):
    for c in CANON[WIN[i]]:
        top3[i, c-1] = True
first = np.zeros(N, int)
for i in np.flatnonzero(M):
    first[i] = CANON[WIN[i]][0]
def g(arr2, k):  # 軸k(0-index) の値
    return np.array([arr2[i, order[i, k]-1] for i in range(N)])
p3a = [g(P3, k) for k in range(4)]
in3 = [g(top3, k) for k in range(4)]
win1 = [(first == order[:, k]) for k in range(4)]
EXP = M & (DATE >= "2024-07-01") & (DATE <= "2025-12-31")
CON = M & (DATE >= "2026-01-01")
qs = np.quantile(p3a[0][EXP], np.linspace(0, 1, 11))
p("p3[軸1] 十分位の境界（探索窓）:", np.round(qs, 3))
for name, W in (("探索 2024-07〜2025-12", EXP), ("確認 2026-01〜", CON)):
    p(f"\n=== {name}  n={W.sum()}")
    p(f"{'十分位':<6}{'n':>6}{'軸1予':>7}{'軸1実':>7}{'軸2予':>7}{'軸2実':>7}{'軸3予':>7}{'軸3実':>7}"
      f"{'軸1 1着':>8}{'軸2 1着':>8}{'軸1のみ圏外':>11}{'軸2のみ圏外':>11}{'両方圏外':>9}{'axis_sum':>9}  型A..F%")
    d = np.digitize(p3a[0], qs[1:-1])
    for b in range(10):
        s = W & (d == b)
        n = s.sum()
        if n == 0: continue
        r = lambda a: a[s].mean()*100
        only1 = (~in3[0] & in3[1])[s].mean()*100
        only2 = (in3[0] & ~in3[1])[s].mean()*100
        both = (~in3[0] & ~in3[1])[s].mean()*100
        tdist = " ".join(f"{(TYPE[s]==t).mean()*100:.0f}" for t in "ABCDEF")
        p(f"D{b+1:<5}{n:>6}{p3a[0][s].mean()*100:>7.1f}{r(in3[0]):>7.1f}{p3a[1][s].mean()*100:>7.1f}{r(in3[1]):>7.1f}"
          f"{p3a[2][s].mean()*100:>7.1f}{r(in3[2]):>7.1f}{r(win1[0]):>8.1f}{r(win1[1]):>8.1f}{only1:>11.1f}{only2:>11.1f}{both:>9.1f}"
          f"{AX[s].mean():>9.3f}  {tdist}")
    # 実3着内率の差 軸1-軸2 の CI（下位3十分位）
    rng = np.random.default_rng(0)
    for lo in (1, 2, 3):
        s = np.flatnonzero(W & (d < lo))
        dif = in3[0][s].astype(float) - in3[1][s].astype(float)
        bs = [dif[rng.integers(0, len(s), len(s))].mean() for _ in range(2000)]
        p(f"  下位{lo*10}%  軸1実−軸2実 = {dif.mean()*100:+.2f}pt  95%CI [{np.percentile(bs,2.5)*100:+.2f}, {np.percentile(bs,97.5)*100:+.2f}]  n={len(s)}")
