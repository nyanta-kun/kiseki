#!/usr/bin/env python3
"""A. 旧ランク3モデルの出力は「市場にも型ラボにも無い予測」か（2026-09-11）。

台: /tmp/old_rank_ideas_rows.pkl（`old_rank_ideas_build.py`）
窓: 探索 2024-07〜2025-12 / 確認 2026-01〜2026-08
"""
from __future__ import annotations

import pickle
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import roc_auc_score

R = pd.DataFrame(pickle.load(open("/tmp/old_rank_ideas_rows.pkl", "rb")))
R["win"] = np.where(R["date"] < "2026-01-01", "探索", "確認")
R["bust"] = ~R["a1_in3"]
R["nosplit"] = ~R["both_in3"]          # 軸2車がそろわない
# 🔴 `wt_race_payouts` は 2026-07-04 で更新が止まっている。確定三連単オッズは
#    `wt_odds`（決着の目）から引き直したものを使う（/tmp/tf_odds_win.pkl）。
_tf = pickle.load(open("/tmp/tf_odds_win.pkl", "rb"))
R["tf_odds"] = R["race_key"].map(_tf)
R["tf_pay"] = R["tf_odds"] * 100
R["u30"] = R["tf_odds"] >= 30
R["u100"] = R["tf_odds"] >= 100
print(f"台 {len(R):,}行  探索 {(R.win=='探索').sum():,} / 確認 {(R.win=='確認').sum():,}")
print(f"bust_p 欠損 {R.bust_p.isna().mean()*100:.1f}%（軸1≠WT◎は母集団外）  "
      f"upset_p 欠損 {R.upset_p.isna().mean()*100:.1f}%  払戻欠損 {R.tf_odds.isna().mean()*100:.1f}%")
print(f"基準率: bust {R.bust.mean()*100:.2f}% / 軸2そろわず {R.nosplit.mean()*100:.2f}% / "
      f"30倍+ {R.u30.mean()*100:.2f}% / 100倍+ {R.u100.mean()*100:.2f}%")

EXIST = ["pw_ent", "axis_sum", "arare", "gap", "pw_gap12", "rp_sd"]
NEW = ["bust_p", "upset_p", "a1_pbad"]

print("\n## 1. 順位相関（Spearman・全期間）")
sub = R[EXIST + NEW].dropna()
print(sub.corr(method="spearman").round(3).to_string())

print("\n## 2. 単一量の AUC（既存量と必ず並べる）")
tgts = ["bust", "nosplit", "u30", "u100"]
for w in ("探索", "確認"):
    d = R[R.win == w]
    print(f"\n[{w}]  n={len(d):,}")
    hdr = "量".ljust(10) + "".join(t.rjust(10) for t in tgts)
    print(hdr)
    for c in EXIST + NEW:
        cells = []
        for t in tgts:
            m = d[[c, t]].dropna()
            m = m[m[t].notna()]
            if len(m) < 200 or m[t].nunique() < 2:
                cells.append("-".rjust(10)); continue
            a = roc_auc_score(m[t].astype(int), m[c])
            cells.append(f"{max(a,1-a):.4f}".rjust(10))
        print(c.ljust(10) + "".join(cells))
    print("（AUC は向きを揃えて max(a,1-a) で表示）")

print("\n## 3. 既存量で説明した残差（線形・全期間）")
for c in NEW:
    m = R[EXIST + [c]].dropna()
    X, y = m[EXIST].values, m[c].values
    lr = LinearRegression().fit(X, y)
    r2 = lr.score(X, y)
    res = y - lr.predict(X)
    print(f"{c:10s} R²={r2:.4f}  残差SD={res.std():.4f} (元SD {y.std():.4f})")

print("\n## 4. 残差だけで目的を当てられるか（AUC）")
for c in NEW:
    m = R[EXIST + [c, "bust", "nosplit", "u30", "win"]].dropna()
    tr, te = m[m.win == "探索"], m[m.win == "確認"]
    lr = LinearRegression().fit(tr[EXIST].values, tr[c].values)
    for t in ("bust", "nosplit", "u30"):
        rs = te[c].values - lr.predict(te[EXIST].values)
        a = roc_auc_score(te[t].astype(int), rs)
        print(f"{c:10s} 残差→{t:8s} AUC={a:.4f} (n={len(te):,})")

print("\n## 5. 増分 AUC（探索で学習 → 確認で評価・ロジスティック）")
for t in ("bust", "nosplit", "u30", "u100"):
    m = R[EXIST + NEW + [t, "win"]].dropna()
    tr, te = m[m.win == "探索"], m[m.win == "確認"]
    if len(te) < 500: continue
    def auc(cols):
        s = LogisticRegression(max_iter=2000).fit(
            (tr[cols] - tr[cols].mean()) / tr[cols].std(), tr[t].astype(int))
        z = (te[cols] - tr[cols].mean()) / tr[cols].std()
        return roc_auc_score(te[t].astype(int), s.predict_proba(z)[:, 1])
    a0, a1 = auc(EXIST), auc(EXIST + NEW)
    ab = auc(EXIST + ["bust_p"]); au = auc(EXIST + ["upset_p"])
    print(f"{t:8s} 既存6量 {a0:.4f} → +bust_p {ab:.4f} (+{ab-a0:+.4f}) "
          f"+upset_p {au:.4f} ({au-a0:+.4f}) +全部 {a1:.4f} ({a1-a0:+.4f})  n_te={len(te):,}")

print("\n## 6. 軸選定（7S 3ヘッド・簡易版）vs 型ラボ（p3 上位2車）")
print("   🔴 本番関数と重み(0.3)で測り直したのは old_rank_ideas_axis.py。doc §8 はそちらの値。")
for w in ("探索", "確認"):
    d = R[R.win == w]
    same = (d.a1 == d.a1_3h) & (d.a2 == d.a2_3h)
    print(f"[{w}] n={len(d):,}  軸2車が一致 {same.mean()*100:.1f}%")
    print(f"   軸2そろい  型ラボ {d.both_in3.mean()*100:.2f}%  3ヘッド {d.both_in3_3h.mean()*100:.2f}%  "
          f"差 {(d.both_in3_3h.mean()-d.both_in3.mean())*100:+.2f}pt")
    dd = d[~same]
    if len(dd) > 100:
        print(f"   入れ替わる {len(dd):,}件だけで: 型ラボ {dd.both_in3.mean()*100:.2f}% "
              f"↔ 3ヘッド {dd.both_in3_3h.mean()*100:.2f}%  差 {(dd.both_in3_3h.mean()-dd.both_in3.mean())*100:+.2f}pt")
    print(f"   軸1が3着内 型ラボ {d.a1_in3.mean()*100:.2f}%  3ヘッド {d.a1_in3_3h.mean()*100:.2f}%")

print("\n## 7. 荒れ度: arare vs upset_p（配当を分けるか）")
for w in ("探索", "確認"):
    d = R[(R.win == w) & R.tf_odds.notna()].copy()
    print(f"\n[{w}] n={len(d):,}")
    d["ar5"] = pd.cut(d.arare, [-9, -1, 0, 1, 2, 9], labels=["<=-1", "0", "1", "2", ">=3"])
    d["up5"] = pd.qcut(d.upset_p, 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
    for col in ("ar5", "up5"):
        g = d.groupby(col, observed=True).agg(
            n=("tf_odds", "size"), 中央倍率=("tf_odds", "median"),
            p30=("u30", "mean"), p100=("u100", "mean"), そろい=("both_in3", "mean"))
        g["中央倍率"] = g["中央倍率"].round(1)
        g["30倍+%"] = (g.p30 * 100).round(1); g["100倍+%"] = (g.p100 * 100).round(1)
        g["そろい%"] = (g["そろい"] * 100).round(1)
        print(col, "\n", g[["n", "中央倍率", "30倍+%", "100倍+%", "そろい%"]].to_string())
