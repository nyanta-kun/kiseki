#!/usr/bin/env python3
"""機序: (A) 前日の決着傾向は今日の ex-ante 量と相関しているか (B) day_index の交絡
(C) 「前日のサプライズ（実績−ex-ante期待）」は翌日へ持ち越すか（自己相関・分散分解）
(D) サプライズを既存量へ足した増分"""
import os, numpy as np, pandas as pd
from scipy.stats import spearmanr, pearsonr
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score
D = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(1)
f = pd.read_pickle(os.path.join(D, "analysis.pkl"))
BASE = ["axis_sum", "arare", "pw_ent"]

print("## (A) 前日/開催の決着傾向 × 今日の ex-ante 量（Spearman・全窓 n=%d）" % f.r_prev_ltf_med.notna().sum())
for col in ["r_prev_ltf_med", "r_cup_ltf", "r_prev_nige", "r_prev_idx1"]:
    g = f[f[col].notna()]
    print(f"  {col:15s} " + "  ".join(f"{x}: ρ={spearmanr(g[col], g[x])[0]:+.3f}" for x in BASE + ["day_index", "v365_ltf"]))
print("\n## (B) day_index 別の r_prev_ltf_med 平均 / arare 平均 / そろい%")
print(f.groupby("day_index").agg(n=("both3", "size"), r_prev_ltf_med=("r_prev_ltf_med", "mean"), arare=("arare", "mean"), both3=("both3", "mean"), pay_med=("lpay", lambda s: np.exp(s.median()))).round(3))

# ── (C) サプライズ ──
ex = f[f.win == "explore"]
def X(g): return g[BASE].values
lg = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X(ex), ex.both3)
rd = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(X(ex), ex.lpay)
f["e_both3"] = lg.predict_proba(X(f))[:, 1]
f["e_lpay"] = rd.predict(X(f))
f["s_both3"] = f.both3 - f.e_both3
f["s_lpay"] = f.lpay - f.e_lpay
vd = f.groupby(["venue", "date"]).agg(cup=("cup_id", "first"), day_index=("day_index", "first"), n=("both3", "size"),
                                      s_both3=("s_both3", "mean"), s_lpay=("s_lpay", "mean"),
                                      noise_both3=("e_both3", lambda p: (p * (1 - p)).mean()), win=("win", "first")).reset_index()
resid_var_lpay = f[f.win == "explore"].s_lpay.var()
vd["noise_both3"] = vd.noise_both3 / vd.n
vd["noise_lpay"] = resid_var_lpay / vd.n
print("\n## (C) 会場×日のサプライズ（実績 − ex-ante 期待の日平均）")
for w in ("explore", "confirm"):
    g = vd[(vd.win == w) & (vd.n >= 6)]
    for c in ("both3", "lpay"):
        v_obs = g[f"s_{c}"].var(); v_noise = g[f"noise_{c}"].mean()
        print(f"  [{w}] {c}: 日平均サプライズの分散 {v_obs:.5f} / 二項・残差ノイズの期待値 {v_noise:.5f} → 日固有の分散 = {v_obs - v_noise:+.5f}"
              f"（説明できる割合 {(v_obs - v_noise) / v_obs * 100:+.1f}%・日数 {len(g)}）")
# 前日サプライズ → 当日サプライズ（同一開催）
p = vd[["venue", "date", "cup", "s_both3", "s_lpay"]].copy(); p["date"] = p.date + pd.Timedelta(days=1)
p = p.rename(columns={"s_both3": "ps_both3", "s_lpay": "ps_lpay", "cup": "pcup"})
vd2 = vd.merge(p, on=["venue", "date"]); vd2 = vd2[vd2.cup == vd2.pcup]
for w in ("explore", "confirm"):
    g = vd2[vd2.win == w]
    for c in ("both3", "lpay"):
        r, pv = pearsonr(g[f"ps_{c}"], g[f"s_{c}"])
        bs = [pearsonr(*[g.iloc[i][[f"ps_{c}", f"s_{c}"]].values.T for i in [rng.integers(0, len(g), len(g))]][0])[0] for _ in range(300)]
        print(f"  [{w}] 前日→当日 サプライズ自己相関 {c}: r={r:+.4f} 95%CI[{np.percentile(bs,2.5):+.4f},{np.percentile(bs,97.5):+.4f}] p={pv:.2g} (会場日 n={len(g)})")

# ── (D) 増分 ──
f = f.merge(vd2[["venue", "date", "ps_both3", "ps_lpay"]], on=["venue", "date"], how="left")
# 開催ここまでのサプライズ（前日までの累積平均）
cs = vd.sort_values("date").groupby("cup")
vd["c_both3"] = cs.s_both3.transform(lambda s: s.shift(1).expanding().mean())
vd["c_lpay"] = cs.s_lpay.transform(lambda s: s.shift(1).expanding().mean())
f = f.merge(vd[["venue", "date", "c_both3", "c_lpay"]], on=["venue", "date"], how="left")
def cv(g, cols, y):
    Xg = g[cols].copy()
    for c in cols:
        if Xg[c].isna().any():
            Xg[c + "_na"] = Xg[c].isna().astype(float); Xg[c] = Xg[c].fillna(0.0)
    Xg = Xg.values; yy = g[y].values; pred = np.zeros(len(g))
    for tr, te in GroupKFold(5).split(Xg, yy, g.date.values):
        pred[te] = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(Xg[tr], yy[tr]).predict_proba(Xg[te])[:, 1]
    return pred
def dauc(y, a, b, n=500):
    y = np.asarray(y); d = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y)); d.append(roc_auc_score(y[i], b[i]) - roc_auc_score(y[i], a[i]))
    return roc_auc_score(y, b) - roc_auc_score(y, a), np.percentile(d, 2.5), np.percentile(d, 97.5)
print("\n## (D) サプライズを既存量へ足した増分（logit 5-fold・ΔAUC 95%CI）")
for y in ("both3", "big50"):
    for w in ("explore", "confirm"):
        g = f[f.win == w]; b = cv(g, BASE, y)
        for name, cols in (("+前日サプライズ2", ["ps_both3", "ps_lpay"]), ("+開催サプライズ2", ["c_both3", "c_lpay"]), ("+両方", ["ps_both3", "ps_lpay", "c_both3", "c_lpay"]), ("+day_index(対照)", ["day_index"])):
            d, lo, hi = dauc(g[y].values, b, cv(g, BASE + cols, y))
            print(f"  [{y}] {w:8s} {name:14s} ΔAUC {d:+.4f} [{lo:+.4f},{hi:+.4f}]")
# 記述: 前日サプライズ五分位 → 当日そろい%
print("\n## 記述: 前日サプライズ(both3) 五分位 → 当日そろい% / PAY中央（同一開催のみ）")
for w in ("explore", "confirm"):
    g = f[(f.win == w) & f.ps_both3.notna()]
    q = pd.qcut(g.ps_both3, 5, labels=False)
    t = g.groupby(q).agg(n=("both3", "size"), both3=("both3", "mean"), e=("e_both3", "mean"), pay=("lpay", lambda s: np.exp(s.median())))
    print(f"  [{w}] n={len(g)} 実績そろい% " + " ".join(f"{v*100:5.1f}" for v in t.both3) + " | ex-ante期待% " + " ".join(f"{v*100:5.1f}" for v in t.e) + " | PAY中央 " + " ".join(f"{v:5.0f}" for v in t.pay))
