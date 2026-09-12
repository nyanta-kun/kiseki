#!/usr/bin/env python3
"""四半期 walk-forward（vintage）でレース単位「そろい」スコアを作り、本番確率 p_both と並べる。

- 学習は 2024-07-01 〜 その四半期の前日。予測は 2025Q1〜2026Q3（2024年は学習のみ）。
- 対照は **本番が買い目に使う確率から作った `p_both`**（`rank_7t3_blend_probs`・λ/μ込み・
  /tmp/p_both_blend.npz）と、板の PL 周辺積 `p_both_pl`。型判定が使う `axis_sum` は周辺和。
- 増分は「既存6量 + logit(p_both)」のロジスティックへ足したときの AUC 差（paired bootstrap CI）。

出力: scores.pkl  (T に s_full / s_rule / p_both / p_both_pl / 各ロジスティック予測を足したもの)
"""
from __future__ import annotations

import pickle
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
D = pickle.load((HERE / "table.pkl").open("rb"))
T: pd.DataFrame = D["T"].reset_index(drop=True)
FEAT = D["feat_cols"]
EXIST = ["axis_sum", "pw_ent", "arare", "gap", "pw_gap12", "rp_sd"]
G1 = (["p3_a1", "p3_a2", "pw_a1", "pw_a2", "p3_prod", "p3_min2", "p3_gap12", "p3_gap23",
       "p3_sum3", "p3_ent", "p3_total", "pw_sum2"]
      + [f"p3s{k}" for k in range(7)] + [f"pws{k}" for k in range(7)])
G2 = ["same_line", "adjacent", "a1_leads_a2", "a2_leads_a1", "a1_leader", "a2_leader",
      "a1_lsize", "a2_lsize", "a1_lpos", "a2_lpos", "a1_mark", "a2_mark", "a1_rp", "a2_rp",
      "a1_rprank", "a2_rprank", "a1_beh", "a2_beh", "a1_st_oi", "a2_st_oi", "a1_st_nige",
      "a2_st_nige", "n_lines", "n_solo", "max_lsize", "rp_top_gap"]
G3 = [f"{p}_{c}" for c in FEAT for p in ("m", "s", "x", "n", "a1", "a2")]
G4 = ["dayi"]
ALL = EXIST + G1 + G2 + G3 + G4
SEEDS = [0, 1, 2, 3, 4]
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=100,
              feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, num_threads=4)
QUARTERS = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
            ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30"),
            ("2026-07-01", "2026-09-30")]


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def auc(y, s):
    return roc_auc_score(y, s)


def paired_boot(y, s_a, s_b, n=1000, seed=0):
    rng = np.random.default_rng(seed)
    y = np.asarray(y); s_a = np.asarray(s_a); s_b = np.asarray(s_b)
    out, N = [], len(y)
    for _ in range(n):
        ix = rng.integers(0, N, N)
        out.append(auc(y[ix], s_b[ix]) - auc(y[ix], s_a[ix]))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def fit_logistic(Xtr, ytr, Xte, C_=1.0):
    sc = StandardScaler().fit(Xtr)
    m = LogisticRegression(C=C_, max_iter=3000).fit(sc.transform(Xtr), ytr)
    return m.predict_proba(sc.transform(Xte))[:, 1]


def n_rounds(Xtr, ytr, dtr, seed):
    dn = dtr.astype("datetime64[D]").astype(np.int64)
    cut = np.quantile(dn, 0.8)
    m = dn <= cut
    b = lgb.train(dict(PARAMS, seed=seed), lgb.Dataset(Xtr[m], ytr[m]), 3000,
                  valid_sets=[lgb.Dataset(Xtr[~m], ytr[~m])],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    return max(int(b.best_iteration), 50)


def main() -> None:
    # 本番確率 p_both（板の index で引く）
    b = np.load("/tmp/p_both_blend.npz")
    T["p_both"] = b["p_both"][T.i.values]
    z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
    PROB = z["PROB"]
    import itertools
    CANON = list(itertools.permutations(range(1, 8), 3))
    mask = np.zeros((len(CANON), 8, 8), dtype=bool)
    for t, p in enumerate(CANON):
        for xx in p:
            for yy in p:
                if xx != yy:
                    mask[t, xx, yy] = True
    pbl = np.empty(len(T))
    for r, (i, a1, a2) in enumerate(zip(T.i.values, T.a1.values, T.a2.values)):
        pbl[r] = PROB[i][mask[:, a1, a2]].sum()
    T["p_both_pl"] = pbl
    T["lp_both"] = logit(T.p_both)
    T["lp_both_pl"] = logit(T.p_both_pl)

    for c in ("s_full", "s_struct", "l_exist", "l_exist_pb", "l_rule", "l_exist_pb_full"):
        T[c] = np.nan
    y_all = T.y.values.astype(int)
    dates = T.date.values
    for qlo, qhi in QUARTERS:
        tr = (dates >= "2024-07-01") & (dates < qlo)
        te = (dates >= qlo) & (dates <= qhi)
        if te.sum() == 0:
            continue
        ytr = y_all[tr]
        # ロジスティック群（vintage）
        T.loc[te, "l_exist"] = fit_logistic(T.loc[tr, EXIST].values, ytr, T.loc[te, EXIST].values)
        cols = EXIST + ["lp_both"]
        T.loc[te, "l_exist_pb"] = fit_logistic(T.loc[tr, cols].values, ytr, T.loc[te, cols].values)
        cols = EXIST + ["lp_both", "same_line", "adjacent", "a1_leads_a2"]
        T.loc[te, "l_rule"] = fit_logistic(T.loc[tr, cols].values, ytr, T.loc[te, cols].values)
        # LightGBM 全部 / 構造のみ（5seed 平均）
        for name, cols_ in (("s_full", ALL), ("s_struct", G2 + G3 + G4)):
            Xtr, Xte = T.loc[tr, cols_].values, T.loc[te, cols_].values
            ps = []
            for sd in SEEDS:
                r = n_rounds(Xtr, ytr, dates[tr], sd)
                m = lgb.train(dict(PARAMS, seed=sd), lgb.Dataset(Xtr, ytr), r)
                ps.append(m.predict(Xte))
            T.loc[te, name] = np.mean(ps, axis=0)
        # スタッキング: 既存6量+p_both + logit(s_full)。学習窓内の OOF は四半期WF で
        # 既に埋まっている（前の四半期の予測）ので、それを使う（最初の四半期は不可）
        have = tr & ~T["s_full"].isna().values
        if have.sum() > 2000:
            cols = EXIST + ["lp_both"]
            Xtr2 = np.c_[T.loc[have, cols].values, logit(T.loc[have, "s_full"].values)]
            Xte2 = np.c_[T.loc[te, cols].values, logit(T.loc[te, "s_full"].values)]
            T.loc[te, "l_exist_pb_full"] = fit_logistic(Xtr2, y_all[have], Xte2)
        print(f"  {qlo}〜{qhi}  学習 {tr.sum():,}  予測 {te.sum():,}  "
              f"AUC axis {auc(y_all[te], T.loc[te,'axis_sum']):.4f} "
              f"p_both {auc(y_all[te], T.loc[te,'p_both']):.4f} "
              f"s_full {auc(y_all[te], T.loc[te,'s_full']):.4f}", flush=True)

    pickle.dump(T, (HERE / "scores.pkl").open("wb"))
    print("→ scores.pkl")

    print("\n## AUC（vintage・四半期WF）  探索OOS = 2025-01〜12 / 確認 = 2026-01〜08")
    wins = {"探索": (dates >= "2025-01-01") & (dates <= "2025-12-31"),
            "確認": (dates >= "2026-01-01")}
    cols = ["axis_sum", "p3_prod", "p_both_pl", "p_both", "l_exist", "l_exist_pb", "l_rule",
            "s_struct", "s_full", "l_exist_pb_full"]
    print(f"{'量/腕':18s} {'探索':>8s} {'確認':>8s}")
    for c in cols:
        vals = []
        for w, m in wins.items():
            mm = m & ~T[c].isna().values
            vals.append(auc(y_all[mm], T.loc[mm, c].values))
        print(f"{c:18s} {vals[0]:8.4f} {vals[1]:8.4f}")
    print("\n## 増分（対 既存6量+logit(p_both) ロジスティック）paired bootstrap 95%CI")
    for c in ("l_rule", "s_struct", "s_full", "l_exist_pb_full"):
        for w, m in wins.items():
            mm = m & ~T[c].isna().values & ~T["l_exist_pb"].isna().values
            ci = paired_boot(y_all[mm], T.loc[mm, "l_exist_pb"].values, T.loc[mm, c].values)
            d = auc(y_all[mm], T.loc[mm, c].values) - auc(y_all[mm], T.loc[mm, "l_exist_pb"].values)
            print(f"  {c:16s} {w} n={mm.sum():,}  Δ {d:+.4f}  CI[{ci[0]:+.4f},{ci[1]:+.4f}]")
    print("\n## 順位相関（確認窓）")
    m = wins["確認"]
    for c in ("s_full", "s_struct", "l_rule"):
        print(f"  {c:10s} ρ(axis_sum)={spearmanr(T.loc[m,c], T.loc[m,'axis_sum']).correlation:+.3f}"
              f"  ρ(p_both)={spearmanr(T.loc[m,c], T.loc[m,'p_both']).correlation:+.3f}"
              f"  ρ(pw_ent)={spearmanr(T.loc[m,c], T.loc[m,'pw_ent']).correlation:+.3f}")
    print("\n## 較正: p_both 十分位 × 同ライン の 実測そろい率 − p_both（確認窓）")
    e = T[m].copy()
    e["q"] = pd.qcut(e.p_both, 10, labels=False)
    g = e.groupby(["q", "same_line"]).apply(lambda s: pd.Series(dict(n=len(s), pb=s.p_both.mean(),
                                                                       act=s.y.mean())))
    g["resid_pt"] = (g.act - g.pb) * 100
    print(g.round(3).to_string())


if __name__ == "__main__":
    main()
