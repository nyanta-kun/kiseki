#!/usr/bin/env python3
"""A2. `arare`(荒れ度) と `upset_screen` を **軸の堅さを固定して**比べる（2026-09-11）。

型ラボの設計（`DESIGN.md` 1.1）は「①軸の堅さ＝的中率 / ②荒れ度＝配当」の2軸。
②の代替を名乗るには **axis_sum を固定したうえで配当を分ける**必要がある。
"""
from __future__ import annotations
import pickle
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

R = pd.DataFrame(pickle.load(open("/tmp/old_rank_ideas_rows.pkl", "rb")))
R["win"] = np.where(R["date"] < "2026-01-01", "探索", "確認")
_tf = pickle.load(open("/tmp/tf_odds_win.pkl", "rb"))
R["tf_odds"] = R["race_key"].map(_tf)
R = R[R.tf_odds.notna()].copy()
R["u30"] = R.tf_odds >= 30
R["u100"] = R.tf_odds >= 100
R["firm"] = R.axis_sum >= 1.44

print("## 1. axis_sum を5分位で固定したうえで、配当を分ける力（AUC on 100倍+）")
for w in ("探索", "確認"):
    d = R[R.win == w].copy()
    d["as5"] = pd.qcut(d.axis_sum, 5, labels=[1, 2, 3, 4, 5])
    print(f"\n[{w}] n={len(d):,}")
    print("axis_sum五分位 |  arare  upset_p  pw_ent    gap  |  100倍+基準率")
    for q in [1, 2, 3, 4, 5]:
        s = d[d.as5 == q]
        cells = []
        for c in ("arare", "upset_p", "pw_ent", "gap"):
            a = roc_auc_score(s.u100.astype(int), s[c])
            cells.append(f"{a:.4f}")
        print(f"   Q{q} n={len(s):5d}  " + "  ".join(cells) + f"   {s.u100.mean()*100:5.2f}%")
    print("（向きは揃えていない。0.5未満なら逆向き）")

print("\n## 2. 型の6セルを `upset_p` の3分割で作り直したらどうなるか")
for w in ("探索", "確認"):
    d = R[R.win == w].copy()
    lo, hi = d.upset_p.quantile([1/3, 2/3])
    d["u3"] = np.where(d.upset_p <= lo, "低", np.where(d.upset_p >= hi, "高", "中"))
    d["a3"] = np.where(d.arare <= -1, "低", np.where(d.arare >= 1, "高", "中"))
    print(f"\n[{w}]")
    for col, name in (("a3", "現行 arare"), ("u3", "upset_p 3分割")):
        g = d.groupby(["firm", col], observed=True).agg(
            n=("tf_odds", "size"), 中央倍率=("tf_odds", "median"),
            そろい=("both_in3", "mean"), p100=("u100", "mean"))
        g["そろい%"] = (g.そろい * 100).round(1); g["100倍+%"] = (g.p100 * 100).round(1)
        print(f"-- {name}")
        print(g[["n", "中央倍率", "そろい%", "100倍+%"]].round(1).to_string())

print("\n## 3. 荒れ度として使うなら「軸の堅さと独立」であってほしい")
for w in ("探索", "確認"):
    d = R[R.win == w]
    print(f"[{w}] corr(axis_sum, arare)={d.axis_sum.corr(d.arare, method='spearman'):+.3f}  "
          f"corr(axis_sum, upset_p)={d.axis_sum.corr(d.upset_p, method='spearman'):+.3f}  "
          f"corr(arare, upset_p)={d.arare.corr(d.upset_p, method='spearman'):+.3f}")

print("\n## 4. upset_p の残差（axis_sum・pw_ent を抜いたあと）は配当を分けるか")
from sklearn.linear_model import LinearRegression
tr, te = R[R.win == "探索"], R[R.win == "確認"]
X = ["axis_sum", "pw_ent", "pw_gap12", "gap", "rp_sd"]
lr = LinearRegression().fit(tr[X], tr.upset_p)
res_te = te.upset_p - lr.predict(te[X])
res_ar = te.arare - LinearRegression().fit(tr[X], tr.arare).predict(te[X])
for nm, v in (("upset_p 残差", res_te), ("arare 残差", res_ar)):
    print(f"{nm}: 100倍+ AUC={roc_auc_score(te.u100.astype(int), v):.4f}  "
          f"30倍+ AUC={roc_auc_score(te.u30.astype(int), v):.4f}  "
          f"そろい AUC={roc_auc_score((~te.both_in3).astype(int), v):.4f}")
