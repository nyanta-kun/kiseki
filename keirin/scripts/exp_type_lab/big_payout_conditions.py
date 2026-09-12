#!/usr/bin/env python3
"""三連単10万円+ が出るレースの条件（落車・失格レースを除く）— 2026-09-04。

母集団: /tmp/race_type_board.npz の7車 36,427R（2024-07〜2026-08・vintage p3/pw）
       から **DNF（発走後の落車・失格）レースを除外**（/tmp/dnf_races.json）。
目的  : 三連単の確定払戻 >= 100,000円（100円あたり）が起きるレースの事前条件。
窓    : 探索 2024-07〜2025-12 / 確認 2026-01〜2026-08。**両窓で同じ向きかだけを見る**。
"""
from __future__ import annotations
import json, sys
import numpy as np

z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
DNF = set(json.load(open("/tmp/dnf_races.json")))
KEY, DATE, PAY = z["KEY"], z["DATE"], z["PAY"]
P3, PW, RP = z["P3"].astype(float), z["PW"].astype(float), z["A_race_point"].astype(float)
NL = z["A_n_lines"].astype(float)

base = (z["TYPE"] != "") & (z["WIN"] >= 0) & np.isfinite(PAY) & z["OKPRED"]
nodnf = np.array([str(k) not in DNF for k in KEY])
M = base & nodnf
IDX = np.flatnonzero(M)
BIG = PAY >= 100_000

def ent(p):
    p = np.clip(p, 1e-9, None); p = p / p.sum()
    return float(-(p * np.log(p)).sum() / np.log(len(p)))

F = {}
o3 = -np.sort(-P3, axis=1)       # 3着内率 降順
ow = -np.sort(-PW, axis=1)       # 1着率 降順
F["pw_max 1位指数の単勝率"] = ow[:, 0]
F["pw_gap 単勝率1位-2位"] = ow[:, 0] - ow[:, 1]
F["pw_ent 単勝率の一様さ"] = np.array([ent(PW[i]) for i in range(len(PW))])
F["p3_max 1位指数の複勝率"] = o3[:, 0]
F["p3_min 最下位指数の複勝率"] = o3[:, 6]
F["p3_4th 4番手の複勝率"] = o3[:, 3]
F["p3_sd 複勝率のSD"] = P3.std(axis=1)
F["n_p3_ge10 複勝率10%+の車数"] = (P3 >= 0.10).sum(axis=1).astype(float)
F["n_p3_ge30 複勝率30%+の車数"] = (P3 >= 0.30).sum(axis=1).astype(float)
F["n_p3_ge40 複勝率40%+の車数"] = (P3 >= 0.40).sum(axis=1).astype(float)
F["axis_sum 上位2車の複勝率和"] = z["AXIS_SUM"].astype(float)
F["gap 相手の開き"] = z["GAP"].astype(float)
F["arare 荒れ度"] = z["ARARE"].astype(float)
F["rp_sd 競走得点のSD"] = RP.std(axis=1)
F["n_lines ライン本数"] = NL[:, 0]

W = {"探索 2024-07〜2025-12": (DATE >= "2024-07-01") & (DATE <= "2025-12-31"),
     "確認 2026-01〜2026-08": (DATE >= "2026-01-01")}

def auc(score, y):
    r = np.argsort(np.argsort(score)) + 1.0
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

print("=" * 96)
print("■ 母集団と基準率（三連単 確定払戻 >= 100,000円 / 100円）")
print("=" * 96)
print(f"  7車・型判定可・予測オッズ有り        : {base.sum():,}R")
print(f"  うち DNF（落車・失格）レース         : {(base & ~nodnf).sum():,}R "
      f"({(base & ~nodnf).sum()/base.sum()*100:.1f}%)  ← 除外")
print(f"  分析母集団（DNF なし）               : {M.sum():,}R")
print(f"    10万+ の発生 {BIG[M].sum():,}R = {BIG[M].mean()*100:.2f}%  "
      f"（DNF ありでは {BIG[base & ~nodnf].mean()*100:.2f}%）")
for wn, wm in W.items():
    m = M & wm
    print(f"    {wn}: {m.sum():,}R  10万+ {BIG[m].sum():,}R = {BIG[m].mean()*100:.2f}%")

print()
print("=" * 96)
print("■ 単一量の識別力（AUC・0.5=情報ゼロ / 両窓で同じ側に出るか）")
print("=" * 96)
print(f"  {'量':30s} {'探索AUC':>8s} {'確認AUC':>8s}   {'10万+の平均':>12s} {'それ以外':>10s}")
rows = []
for name, v in F.items():
    a = [auc(v[M & wm], BIG[M & wm]) for wm in W.values()]
    mb, mo = v[M & BIG].mean(), v[M & ~BIG].mean()
    rows.append((name, a[0], a[1], mb, mo))
for name, a0, a1, mb, mo in sorted(rows, key=lambda r: -abs(r[2] - 0.5)):
    print(f"  {name:30s} {a0:8.3f} {a1:8.3f}   {mb:12.3f} {mo:10.3f}")

print()
print("=" * 96)
print("■ 五分位ごとの 10万+ 発生率（%）— 両窓が同じ向きに単調かを見る")
print("=" * 96)
for name in [r[0] for r in sorted(rows, key=lambda r: -abs(r[2] - 0.5))][:8]:
    v = F[name]
    print(f"\n  {name}")
    for wn, wm in W.items():
        m = M & wm
        qs = np.quantile(v[m], [0.2, 0.4, 0.6, 0.8])
        b = np.digitize(v[m], qs)
        cells = []
        for k in range(5):
            s = b == k
            cells.append(f"{BIG[m][s].mean()*100:5.2f}%({s.sum():5,})")
        print(f"    {wn}  低→高  " + "  ".join(cells))

print()
print("=" * 96)
print("■ カテゴリ別（型ラボの型 / 種別 / 開催日目 / ライン本数）")
print("=" * 96)
for label, arr in [("型", z["TYPE"]), ("種別", z["RTYPE"]), ("グレード", z["GRADE"]),
                   ("開催日目", z["DAYI"].astype(str)), ("ライン本数", NL[:, 0].astype(int).astype(str))]:
    print(f"\n  ── {label} ──")
    for v in sorted(set(arr[M])):
        line = f"    {str(v):14s}"
        for wn, wm in W.items():
            s = M & wm & (arr == v)
            if s.sum() < 60:
                line += f"  {wn[:2]}: (n<60)          "
            else:
                line += f"  {wn[:2]}: {BIG[s].mean()*100:5.2f}% ({s.sum():5,}R)"
        print(line)
