#!/usr/bin/env python3
"""前日・開催の決着傾向が (a) 記述として決着を分けるか (b) 既存量への増分があるか (c) arare 加点で型がどう動くか。"""
import os, sys, itertools, warnings
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score, r2_score
import lightgbm as lgb
warnings.filterwarnings("ignore")
D = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(0)

z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
P3, PW, TRIO_WIN, PAY, TYPE, ARARE, AXIS = z["P3"], z["PW"], z["TRIO_WIN"], z["PAY"], z["TYPE"], z["ARARE"], z["AXIS_SUM"]
DATE, OKPRED, TRIO_PAY = z["DATE"], z["OKPRED"], z["TRIO_PAY"]
CANON3 = list(itertools.combinations(range(1, 8), 3))
f = pd.read_pickle(os.path.join(D, "feat.pkl"))
m = (TYPE != "") & (TRIO_WIN >= 0) & np.isfinite(TRIO_PAY) & OKPRED
f["ok"] = m
o = np.argsort(-P3, axis=1)[:, :2] + 1
win3 = np.array([CANON3[i] if i >= 0 else (0, 0, 0) for i in TRIO_WIN])
f["both3"] = np.array([(o[i, 0] in win3[i]) and (o[i, 1] in win3[i]) for i in range(len(o))]).astype(int)
f["lpay"] = np.log(np.where(PAY > 0, PAY, np.nan))
f["big50"] = (PAY >= 5000).astype(int)
f["big100"] = (PAY >= 10000).astype(int)
f["axis_sum"], f["arare"], f["type"] = AXIS, ARARE, TYPE
p = PW / PW.sum(1, keepdims=True)
f["pw_ent"] = -(np.where(p > 0, p * np.log(np.where(p > 0, p, 1)), 0)).sum(1)
f["win"] = np.where(f.date <= "2025-12-31", "explore", "confirm")
f = f[f.ok].copy()
print("N ok", len(f), f.win.value_counts().to_dict())

PREV = ["r_prev_ltf_med", "r_prev_nige", "r_prev_sashi", "r_prev_makuri", "r_prev_mark1", "r_prev_idx1", "r_prev_same12"]
CUP = ["r_cup_ltf", "r_cup_nige", "r_cup_sashi", "r_cup_makuri", "r_cup_mark1", "r_cup_idx1", "r_cup_same12"]
ABS = ["prev_ltf_med", "prev_nige", "prev_sashi", "prev_mark1", "prev_idx1", "prev_same12"]
CTRL = ["g_prev_ltf", "g_prev_nige", "g_prev_sashi", "g_prev_idx1"]
V365 = ["v365_ltf", "v365_nige", "v365_sashi", "v365_idx1"]

def boot_ci(a, b, n=1000):
    """レース単位の paired bootstrap（差の平均）。"""
    d = np.asarray(a) - np.asarray(b)
    idx = rng.integers(0, len(d), (n, len(d)))
    s = d[idx].mean(1)
    return d.mean(), np.percentile(s, 2.5), np.percentile(s, 97.5)

# ───────────── (a) 記述 ─────────────
print("\n## (a) 記述: 五分位 → 軸2そろい% / 確定三連単 PAY中央 / 100倍+% (窓別)")
for col in PREV + CUP + ["prev_ltf_med", "prev_idx1", "g_prev_ltf", "g_prev_idx1"]:
    for w in ("explore", "confirm"):
        g = f[(f.win == w) & f[col].notna()]
        if len(g) < 500: continue
        try:
            q = pd.qcut(g[col], 5, labels=False, duplicates="drop")
        except ValueError:
            continue
        t = g.groupby(q).agg(n=("both3", "size"), both3=("both3", "mean"), pay_med=("lpay", lambda s: np.exp(s.median())), big100=("big100", "mean"))
        rs1, p1 = spearmanr(g[col], g.both3); rs2, p2 = spearmanr(g[col], g.lpay)
        print(f"{col:18s} {w:8s} n={len(g):6d}  both3% Q1..Q5 = " + " ".join(f"{v*100:5.1f}" for v in t.both3)
              + f" | PAY med = " + " ".join(f"{v:6.0f}" for v in t.pay_med)
              + f" | 100倍+% = " + " ".join(f"{v*100:4.1f}" for v in t.big100)
              + f" | ρ(both3)={rs1:+.4f} p={p1:.2g} ρ(lpay)={rs2:+.4f} p={p2:.2g}")

# ───────────── (b) 増分 ─────────────
print("\n## (b) 既存量への増分 (5-fold GroupKFold by date・窓内 CV・ΔAUC は paired bootstrap 95%CI)")
BASE = ["axis_sum", "arare", "pw_ent"]
def prep(g, cols):
    X = g[cols].copy()
    for c in cols:
        if X[c].isna().any():
            X[c + "_na"] = X[c].isna().astype(float)
            X[c] = X[c].fillna(0.0)
    return X.values

def cv_pred(g, cols, y, kind="logit"):
    X = prep(g, cols); yy = g[y].values; grp = g.date.values
    pred = np.zeros(len(g))
    for tr, te in GroupKFold(5).split(X, yy, grp):
        if kind == "logit":
            mdl = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)).fit(X[tr], yy[tr])
            pred[te] = mdl.predict_proba(X[te])[:, 1]
        elif kind == "ridge":
            mdl = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(X[tr], yy[tr]); pred[te] = mdl.predict(X[te])
        else:
            mdl = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0).fit(X[tr], yy[tr])
            pred[te] = mdl.predict_proba(X[te])[:, 1]
    return pred

def auc_boot(y, pa, pb, n=500):
    """ΔAUC(b−a) の paired bootstrap。"""
    y = np.asarray(y); d = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max(): continue
        d.append(roc_auc_score(y[idx], pb[idx]) - roc_auc_score(y[idx], pa[idx]))
    d = np.array(d)
    return roc_auc_score(y, pb) - roc_auc_score(y, pa), np.percentile(d, 2.5), np.percentile(d, 97.5)

SETS = {"+prev残差7": PREV, "+cup残差7": CUP, "+prev+cup": PREV + CUP, "+prev絶対値6": ABS, "+v365(会場平均)": V365, "+全場前日(対照)": CTRL, "+prev残差+v365": PREV + V365}
for y in ("both3", "big50"):
    for w in ("explore", "confirm"):
        g = f[f.win == w]
        base = cv_pred(g, BASE, y)
        base_l = cv_pred(g, BASE, y, "lgb")
        print(f"\n[{y}] {w} n={len(g)}  base logit AUC={roc_auc_score(g[y], base):.4f}  base LGBM AUC={roc_auc_score(g[y], base_l):.4f}")
        for name, cols in SETS.items():
            pr = cv_pred(g, BASE + cols, y)
            d, lo, hi = auc_boot(g[y].values, base, pr)
            pl = cv_pred(g, BASE + cols, y, "lgb")
            dl, lol, hil = auc_boot(g[y].values, base_l, pl)
            print(f"  {name:18s} logit ΔAUC {d:+.4f} [{lo:+.4f},{hi:+.4f}]   LGBM ΔAUC {dl:+.4f} [{lol:+.4f},{hil:+.4f}]")
# 配当（log PAY）の R²
for w in ("explore", "confirm"):
    g = f[f.win == w]
    base = cv_pred(g, BASE, "lpay", "ridge"); r0 = r2_score(g.lpay, base)
    s = f"\n[lpay R²] {w} base={r0:.4f}"
    for name, cols in SETS.items():
        pr = cv_pred(g, BASE + cols, "lpay", "ridge")
        s += f"  {name}={r2_score(g.lpay, pr) - r0:+.4f}"
    print(s)

# ───────────── (c) arare 加点 ─────────────
print("\n## (c) arare 加点: t=+1 if r_prev_ltf_med > q80(explore), −1 if < q20, else 0 (NaN→0)")
ex = f[f.win == "explore"].r_prev_ltf_med.dropna()
q20, q80 = ex.quantile(0.2), ex.quantile(0.8)
print(f"q20={q20:+.3f} q80={q80:+.3f}  (前日の三連単中央が普段より e^{q80:.2f}={np.exp(q80):.2f}倍 荒れ / {np.exp(q20):.2f}倍 堅い)")
f["t"] = np.where(f.r_prev_ltf_med > q80, 1, np.where(f.r_prev_ltf_med < q20, -1, 0))
f.loc[f.r_prev_ltf_med.isna(), "t"] = 0
f["arare2"] = f.arare + f.t
firm = f.axis_sum >= 1.44
def lab(s, firm):
    return np.where(firm, np.where(s <= -1, "A", np.where(s == 0, "B", "C")), np.where(s <= -1, "D", np.where(s == 0, "E", "F")))
f["type2"] = lab(f.arare2, firm)
assert (lab(f.arare, firm) == f.type).mean() > 0.99, (lab(f.arare, firm) == f.type).mean()
for w in ("explore", "confirm"):
    g = f[f.win == w]
    print(f"\n[{w}] 型分布 現行 → 加点後 (%):")
    a = g.type.value_counts(normalize=True).reindex(list("ABCDEF")) * 100
    b = g.type2.value_counts(normalize=True).reindex(list("ABCDEF")) * 100
    print("   " + "  ".join(f"{t}: {a[t]:5.1f}→{b[t]:5.1f}" for t in "ABCDEF"))
    print(f"   変わるレース: {(g.type != g.type2).mean()*100:.1f}%")
    for nm, col in (("現行", "type"), ("加点後", "type2")):
        t = g.groupby(col).agg(n=("both3", "size"), both3=("both3", "mean"), pay=("lpay", lambda s: np.exp(s.median())), big100=("big100", "mean")).reindex(list("ABCDEF"))
        print(f"   {nm}: そろい% " + " ".join(f"{t.both3[k]*100:5.1f}" for k in "ABCDEF") + " | PAY中央 " + " ".join(f"{t.pay[k]:5.0f}" for k in "ABCDEF") + " | 100倍+% " + " ".join(f"{t.big100[k]*100:4.1f}" for k in "ABCDEF"))
    print(f"   型内で t が分けるか（現行型 × t → そろい% / PAY中央 / n）:")
    for ty in "ABCDEF":
        gg = g[g.type == ty]
        t = gg.groupby("t").agg(n=("both3", "size"), both3=("both3", "mean"), pay=("lpay", lambda s: np.exp(s.median())), big100=("big100", "mean")).reindex([-1, 0, 1])
        # 差の CI: t=+1 vs t=−1（独立2群の bootstrap）
        a1 = gg[gg.t == 1].both3.values; a0 = gg[gg.t == -1].both3.values
        if len(a1) > 30 and len(a0) > 30:
            bs = [rng.choice(a1, len(a1)).mean() - rng.choice(a0, len(a0)).mean() for _ in range(500)]
            ci = f"Δそろい(+1 − −1)={ (a1.mean()-a0.mean())*100:+.2f}pt [{np.percentile(bs,2.5)*100:+.2f},{np.percentile(bs,97.5)*100:+.2f}]"
            b1 = gg[gg.t == 1].lpay.values; b0 = gg[gg.t == -1].lpay.values
            bs2 = [np.median(rng.choice(b1, len(b1))) - np.median(rng.choice(b0, len(b0))) for _ in range(500)]
            ci += f"  ΔlogPAY中央={np.median(b1)-np.median(b0):+.3f} [{np.percentile(bs2,2.5):+.3f},{np.percentile(bs2,97.5):+.3f}]"
        else:
            ci = ""
        print(f"     {ty}: " + " | ".join(f"t={k:+d} n={int(t.n[k]) if not np.isnan(t.n[k]) else 0:5d} そろい{t.both3[k]*100:5.1f} PAY{t.pay[k]:5.0f} 100倍+{t.big100[k]*100:4.1f}" for k in (-1, 0, 1)) + "  " + ci)
f.to_pickle(os.path.join(D, "analysis.pkl"))
