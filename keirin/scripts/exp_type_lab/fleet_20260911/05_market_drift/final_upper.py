#!/usr/bin/env python3
"""05-2 上限検証: **確定**三連複オッズ（＝市場の最終的な読み・入稿時点では存在しない look-ahead）を
「読み」の量にしたとき、既存量（axis_sum / pw_ent / arare / gap / 予測オッズ最安）への増分があるか。
確定オッズで増分が無ければ、早期の板（情報はその部分集合）にも無い。両窓・レース単位 paired bootstrap。
"""
import os, sys, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from mkfeat import CANON, model_trio_prob, market_features, win_entropy, CAR_IN
rng = np.random.default_rng(0)
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
TYPE = z["TYPE"]; OK = z["OKPRED"]; TW = z["TRIO_WIN"]; DATE = z["DATE"]
ok = (TYPE != "") & (TW >= 0) & OK & np.isfinite(z["TRIO_PAY"])
idx = np.flatnonzero(ok)
P3 = z["P3"]; PW = z["PW"]; PROB = z["PROB"]; TPO = z["TRIO_PO"]; TO = z["TRIO_ODDS"]
WIN = z["WIN"]; PAY = z["PAY"]; AXIS = z["AXIS_SUM"]; ARARE = z["ARARE"]; GAP = z["GAP"]; PO = z["PO"]
cache = os.path.join(D, "final_feat.npz")
if os.path.exists(cache):
    c = np.load(cache, allow_pickle=True); F = c["F"].item(); idx = c["idx"]
else:
    F = {}
    rows = []
    for n, i in enumerate(idx):
        pm = model_trio_prob(PROB[i].astype(np.float64))
        d = market_features(TO[i].astype(np.float64), P3[i].astype(np.float64), pm, TPO[i].astype(np.float64), min_fill=35)
        d["pw_ent"] = win_entropy(PW[i].astype(np.float64))
        d["log_min_po"] = float(np.log(np.nanmin(np.where(PO[i] > 0, PO[i], np.nan))))
        rows.append(d)
        if n % 5000 == 0: print("  feat", n, flush=True)
    for k in rows[0]:
        F[k] = np.array([r[k] for r in rows], float)
    np.savez_compressed(cache, F=F, idx=idx)
n = len(idx)
date = DATE[idx]; win = WIN[idx]; pay = PAY[idx]
top3 = np.array([set(CANON[w]) for w in win], dtype=object)
order = np.argsort(-P3[idx], axis=1) + 1
sorou = np.array([(o[0] in t and o[1] in t) for o, t in zip(order, top3)], float)
big = (pay >= 10_000).astype(float)               # 三連単 100倍+
base = dict(axis_sum=AXIS[idx].astype(float), pw_ent=F["pw_ent"], arare=ARARE[idx].astype(float),
            gap=GAP[idx].astype(float), log_min_po=F["log_min_po"])
mk = ["mk_axis", "mk_top2", "agree2", "mk_ent", "resid_pair", "ratio_top", "log_fav", "kl",
      "mk_rank_a1", "mk_rank_a2", "pair_ratio", "mk_a1", "mk_pair"]
win_e = (date >= "2024-07-01") & (date <= "2025-12-31"); win_c = date >= "2026-01-01"
print(f"n={n}  探索 {win_e.sum()}  確認 {win_c.sum()}  そろい率 {sorou.mean()*100:.2f}%  100倍+ {big.mean()*100:.2f}%")

print("\n== A. 記述: 市場量の五分位 → 軸2そろい率 / 三連単払戻中央 / 100倍+率（確認窓）==")
for k in ["mk_axis", "mk_ent", "resid_pair", "ratio_top", "pair_ratio", "kl", "log_fav"]:
    v = F[k]; mm = win_c & np.isfinite(v)
    qs = np.quantile(v[mm], [0.2, 0.4, 0.6, 0.8])
    b = np.digitize(v, qs)
    s = " | ".join(f"Q{j+1}: そろい{sorou[mm & (b==j)].mean()*100:5.1f}% 払戻中央{np.median(pay[mm&(b==j)])*100:8,.0f} 100倍+{big[mm&(b==j)].mean()*100:4.1f}%" for j in range(5))
    print(f"  {k:11s} {s}")
print("  参考 axis_sum  " + " | ".join(f"Q{j+1}: そろい{sorou[win_c & (np.digitize(base['axis_sum'], np.quantile(base['axis_sum'][win_c],[.2,.4,.6,.8]))==j)].mean()*100:5.1f}%" for j in range(5)))
for k in ["agree2"]:
    for v in (0.0, 1.0):
        mm = win_c & (F[k] == v)
        print(f"  agree2={int(v)} n={mm.sum()} そろい {sorou[mm].mean()*100:.1f}% 払戻中央 {np.median(pay[mm])*100:,.0f} 100倍+ {big[mm].mean()*100:.1f}%")

print("\n== B. 二重計上の点検: 市場量 × 既存量の Spearman（確認窓）==")
print("  {:11s}".format("") + " ".join(f"{k:>11s}" for k in base) + f" {'AGREE(印)':>9s}")
AG = z["AGREE"][idx].astype(float)
for k in mk:
    mm = win_c & np.isfinite(F[k])
    rs = [spearmanr(F[k][mm], base[b][mm])[0] for b in base] + [spearmanr(F[k][mm], AG[mm])[0]]
    print(f"  {k:11s}" + " ".join(f"{r:11.3f}" for r in rs))

def oof_auc(X, y, dates, folds=5, seed=0):
    """日付ブロックの5-fold OOF 予測（ロジスティック）。"""
    ud = np.array(sorted(set(dates))); r = np.random.default_rng(seed); r.shuffle(ud)
    fold_of = {d: j % folds for j, d in enumerate(ud)}
    fo = np.array([fold_of[d] for d in dates])
    pred = np.zeros(len(y))
    for f in range(folds):
        tr, te = fo != f, fo == f
        mdl = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
        mdl.fit(X[tr], y[tr]); pred[te] = mdl.predict_proba(X[te])[:, 1]
    return pred

def boot_dauc(y, p0, p1, B=600):
    n = len(y); d = []
    for _ in range(B):
        s = rng.integers(0, n, n)
        if y[s].min() == y[s].max(): continue
        d.append(roc_auc_score(y[s], p1[s]) - roc_auc_score(y[s], p0[s]))
    return np.percentile(d, [2.5, 97.5])

Xb = np.column_stack([base[b] for b in base])
for target, y in (("軸2そろい", sorou), ("三連単100倍+", big)):
    print(f"\n== C. 増分 AUC（目的={target}・ロジスティック・日付ブロック5-fold OOF・paired bootstrap 95%CI）==")
    for wname, wm in (("探索", win_e), ("確認", win_c)):
        mm = wm & np.all(np.isfinite(np.column_stack([F[k] for k in mk])), axis=1)
        p0 = oof_auc(Xb[mm], y[mm], date[mm])
        a0 = roc_auc_score(y[mm], p0)
        print(f"  [{wname} n={mm.sum()}] base AUC {a0:.4f}")
        for k in mk + ["ALL"]:
            add = np.column_stack([F[j] for j in mk]) if k == "ALL" else F[k][:, None]
            p1 = oof_auc(np.column_stack([Xb, add])[mm], y[mm], date[mm])
            a1 = roc_auc_score(y[mm], p1); lo, hi = boot_dauc(y[mm], p0, p1)
            flag = "★" if lo > 0 else ("▼" if hi < 0 else " ")
            print(f"    +{k:11s} AUC {a1:.4f}  Δ {a1-a0:+.4f} [{lo:+.4f},{hi:+.4f}] {flag}")
        # 市場単体
        p2 = oof_auc(np.column_stack([F[j] for j in mk])[mm], y[mm], date[mm])
        print(f"    市場量だけ    AUC {roc_auc_score(y[mm], p2):.4f}")
