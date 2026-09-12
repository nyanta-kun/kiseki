#!/usr/bin/env python3
"""「堅いレースだが軸が飛ぶ」を事前に選別できるか（2026-09-04・ユーザー質問）。

背景（同日の前段）: 10万+ の発生率は
    混戦 = 飛ぶ33% × 飛んで10万+ 3.5%   堅い = 飛ぶ7% × 飛んで10万+ 12%
で積が平ら。**堅い層の中で飛ぶ側だけを取れれば積が上がる**——それが取れるかを測る。

作法
  - 堅い = 本番の `RANK_7C_P3_SUM_MIN = 1.44`（型A/B/C）。
  - 学習は探索窓(2024-07〜2025-12)のみ、評価は確認窓(2026-01〜08)のみ。
  - 落車・失格レースは除外（再現性がないため・ユーザー指定）。
  - 🔴 件数を減らす比較には**無作為対照**を置く（CLAUDE.md）。
"""
from __future__ import annotations
import itertools, json
import numpy as np
import lightgbm as lgb

CANON = list(itertools.permutations(range(1, 8), 3))
FIRM = 1.44
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
DNF = set(json.load(open("/tmp/dnf_races.json")))
KEY, DATE, PAY, WIN = z["KEY"], z["DATE"], z["PAY"], z["WIN"]
P3, PW = z["P3"].astype(float), z["PW"].astype(float)
AX, GAP, ARARE = z["AXIS_SUM"].astype(float), z["GAP"].astype(float), z["ARARE"].astype(float)
LSZ, LPOS, LEAD = (z["A_line_size"].astype(float), z["A_line_pos"].astype(float),
                   z["A_is_line_leader"].astype(float))
MARK, RP, NL = z["A_prediction_mark"].astype(float), z["A_race_point"].astype(float), z["A_n_lines"].astype(float)
BEH, LG, ST = z["BEHIND"].astype(float), z["LG"], z["ST"]
N = len(KEY)

M = ((z["TYPE"] != "") & (WIN >= 0) & np.isfinite(PAY) & z["OKPRED"]
     & np.array([str(k) not in DNF for k in KEY]))
BIG = PAY >= 100_000
rank_of = np.argsort(np.argsort(-P3, axis=1), axis=1) + 1
top3 = np.full((N, 3), -1, int)
for i in np.flatnonzero(M):
    for j, car in enumerate(CANON[WIN[i]]):
        top3[i, j] = rank_of[i, car - 1]
BUST = (top3 != 1).all(axis=1)

a1 = np.argmax(P3, axis=1)                       # 軸1（複勝率1位）の index
o3 = np.argsort(-P3, axis=1)
a2 = o3[:, 1]
ar = np.arange(N)

def ent(p):
    p = np.clip(p, 1e-9, None); p = p / p.sum()
    return -(p * np.log(p)).sum() / np.log(len(p))

same_line = np.array([LG[i][a1[i]] == LG[i][a2[i]] and LG[i][a1[i]] not in ("", "0")
                      for i in range(N)], float)
n_nige = np.array([(ST[i] == "逃").sum() for i in range(N)], float)
lead_beh = np.zeros(N)
for i in range(N):
    g = LG[i][a1[i]]
    mem = [c for c in range(7) if LG[i][c] == g] if g not in ("", "0") else []
    ld = next((c for c in mem if LPOS[i][c] == 1), a1[i])
    lead_beh[i] = BEH[i][ld]

FEATS = {
    "p3_1": P3[ar, a1], "pw_1": PW[ar, a1],
    "p3_1_2": P3[ar, a1] - P3[ar, a2], "pw_1_2": PW[ar, a1] - np.sort(PW, axis=1)[:, -2],
    "axis_sum": AX, "gap": GAP, "arare": ARARE,
    "pw_ent": np.array([ent(PW[i]) for i in range(N)]),
    "p3_sd": P3.std(axis=1), "rp_sd": RP.std(axis=1),
    "a1_line_size": LSZ[ar, a1], "a1_line_pos": LPOS[ar, a1], "a1_is_leader": LEAD[ar, a1],
    "a1_behind": BEH[ar, a1], "a1_mark": MARK[ar, a1],
    "a1_rp_rank": (np.argsort(np.argsort(-RP, axis=1), axis=1) + 1)[ar, a1].astype(float),
    "a1_rp_minus_max": RP[ar, a1] - RP.max(axis=1),
    "lead_behind": lead_beh, "n_lines": NL[:, 0], "n_nige": n_nige,
    "a2_same_line": same_line, "a2_line_size": LSZ[ar, a2],
    "dayi": z["DAYI"].astype(float), "grade_s": (z["GRADE"] == "S1").astype(float),
    "agree": z["AGREE"].astype(float),
}
X = np.column_stack(list(FEATS.values()))
names = list(FEATS)

TR = M & (AX >= FIRM) & (DATE >= "2024-07-01") & (DATE <= "2025-12-31")
TE = M & (AX >= FIRM) & (DATE >= "2026-01-01")
print(f"堅いレース(axis_sum>={FIRM}・DNFなし)  学習 {TR.sum():,}R / 評価 {TE.sum():,}R")
print(f"  軸1が3着外の基準率  学習 {BUST[TR].mean()*100:.2f}%  評価 {BUST[TE].mean()*100:.2f}%")
print(f"  10万+ の基準率      学習 {BIG[TR].mean()*100:.2f}%  評価 {BIG[TE].mean()*100:.2f}%")

ds = lgb.Dataset(X[TR], label=BUST[TR].astype(int), feature_name=names)
m = lgb.train(dict(objective="binary", learning_rate=0.05, num_leaves=15,
                   min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
                   bagging_freq=1, verbose=-1, seed=0), ds, num_boost_round=250)
s_te = m.predict(X[TE])
s_tr = m.predict(X[TR])

def auc(sc, y):
    r = np.argsort(np.argsort(sc)) + 1.0
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

print(f"\n  軸崩壊の検出 AUC  学習(in-sample) {auc(s_tr, BUST[TR]):.3f}  "
      f"**評価(OOS) {auc(s_te, BUST[TE]):.3f}**")
print("  寄与上位:", ", ".join(f"{n}({int(g)})" for n, g in
      sorted(zip(names, m.feature_importance("gain")), key=lambda t: -t[1])[:8]))

print()
print("=" * 100)
print("■ 確認窓(2026)の堅いレースを、軸崩壊スコアの十分位で見る")
print("=" * 100)
idx = np.flatnonzero(TE)
q = np.quantile(s_te, np.arange(0.1, 1.0, 0.1))
b = np.digitize(s_te, q)
print(f"  {'十分位':8s} {'n':>6s} {'軸1が3着外':>11s} {'10万+':>9s} {'払戻中央(飛んだ時)':>18s} {'3万+':>8s} {'30万+':>8s}")
for k in range(10):
    s = idx[b == k]
    ou = BUST[s]; p = PAY[s]
    med = np.median(p[ou]) if ou.sum() else float("nan")
    print(f"  D{k+1:<7d} {len(s):6,} {ou.mean()*100:10.1f}% {BIG[s].mean()*100:8.2f}% "
          f"{med:17,.0f} {(p >= 30_000).mean()*100:7.2f}% {(p >= 300_000).mean()*100:7.2f}%")

print()
print("■ 上位10%/20% と 無作為対照（同数・20seed）の比較")
for topfrac in (0.1, 0.2):
    thr = np.quantile(s_te, 1 - topfrac)
    sel = idx[s_te >= thr]
    rng = np.random.default_rng(0)
    ctrl = [rng.choice(idx, size=len(sel), replace=False) for _ in range(20)]
    def stat(s):
        return (BUST[s].mean() * 100, BIG[s].mean() * 100, (PAY[s] >= 30_000).mean() * 100)
    a = stat(sel)
    cs = np.array([stat(c) for c in ctrl])
    print(f"\n  上位{int(topfrac*100)}% (n={len(sel):,})")
    for j, lab in enumerate(["軸1が3着外", "10万+", "3万+"]):
        wins = int((a[j] > cs[:, j]).sum())
        print(f"    {lab:12s} 選別 {a[j]:6.2f}%   無作為 中央 {np.median(cs[:, j]):6.2f}% "
              f"[{cs[:, j].min():.2f},{cs[:, j].max():.2f}]   対照に勝ち {wins}/20")
