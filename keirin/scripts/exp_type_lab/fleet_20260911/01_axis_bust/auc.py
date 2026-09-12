#!/usr/bin/env python3
"""そろい y に対する 既存量の AUC 天井 と レース単位モデルの増分 AUC（vintage）。

窓（対象レースより未来を使わない）:
  探索評価: 学習 2024-07-01〜2025-06-30 → 評価 2025-07-01〜2025-12-31
  確認評価: 学習 2024-07-01〜2025-12-31 → 評価 2026-01-01〜2026-08
増分は「既存6量のロジスティック」に「モデル予測の logit」を足したロジスティックで測る。
スタッキングの学習には学習窓内の時間ブロック OOF 予測を使う（in-sample 予測で学習しない）。
"""
from __future__ import annotations

import pickle
import sys
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
T: pd.DataFrame = D["T"]
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

WINDOWS = {
    "探索": (("2024-07-01", "2025-06-30"), ("2025-07-01", "2025-12-31")),
    "確認": (("2024-07-01", "2025-12-31"), ("2026-01-01", "2026-12-31")),
}
SEEDS = [0, 1, 2, 3, 4]
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=100,
              feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, num_threads=4)


def _mask(lo, hi):
    return (T["date"] >= lo) & (T["date"] <= hi)


def auc(y, s):
    return roc_auc_score(y, s)


def paired_boot(y, s_a, s_b, n=1000, seed=0):
    rng = np.random.default_rng(seed)
    y = np.asarray(y); s_a = np.asarray(s_a); s_b = np.asarray(s_b)
    out = []
    N = len(y)
    for _ in range(n):
        ix = rng.integers(0, N, N)
        if y[ix].min() == y[ix].max():
            continue
        out.append(auc(y[ix], s_b[ix]) - auc(y[ix], s_a[ix]))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_logistic(Xtr, ytr, Xte):
    sc = StandardScaler().fit(Xtr)
    m = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(Xtr), ytr)
    return m.predict_proba(sc.transform(Xte))[:, 1]


def n_rounds(Xtr, ytr, dtr, seed):
    """学習窓の末尾 20%（時間順）で early stopping して丸め数を決める。"""
    cut = np.quantile(dtr.astype("datetime64[D]").astype(np.int64), 0.8)
    m = dtr.astype("datetime64[D]").astype(np.int64) <= cut
    ds = lgb.Dataset(Xtr[m], ytr[m]); dv = lgb.Dataset(Xtr[~m], ytr[~m])
    b = lgb.train(dict(PARAMS, seed=seed), ds, 3000, valid_sets=[dv],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    return max(int(b.best_iteration), 50)


def lgb_pred(Xtr, ytr, dtr, Xte, seed, rounds=None):
    r = rounds or n_rounds(Xtr, ytr, dtr, seed)
    b = lgb.train(dict(PARAMS, seed=seed), lgb.Dataset(Xtr, ytr), r)
    return b.predict(Xte), r, b


def oof_blocks(Xtr, ytr, dtr, seed, rounds, k=4):
    """学習窓を時間順に k ブロックへ割り、各ブロックを残りで予測（スタッキング用）。"""
    dn = dtr.astype("datetime64[D]").astype(np.int64)
    qs = np.quantile(dn, np.linspace(0, 1, k + 1))
    oof = np.full(len(ytr), np.nan)
    for j in range(k):
        m = (dn >= qs[j]) & (dn <= qs[j + 1]) if j == k - 1 else (dn >= qs[j]) & (dn < qs[j + 1])
        b = lgb.train(dict(PARAMS, seed=seed), lgb.Dataset(Xtr[~m], ytr[~m]), rounds)
        oof[m] = b.predict(Xtr[m])
    return oof


def main() -> None:
    print(f"台 {len(T):,}R  y(そろい) 全体 {T.y.mean()*100:.2f}%")
    print("\n## 1. 単一量の AUC（評価窓・向きは自動で符号合わせ）")
    singles = EXIST + ["p3_prod", "p3_min2", "p3_a1", "p3_a2", "p3_sum3", "pw_sum2", "p3_gap12"]
    print(f"{'量':12s} " + " ".join(f"{w:>8s}" for w in WINDOWS))
    for q in singles:
        vals = []
        for w, (_, (lo, hi)) in WINDOWS.items():
            e = T[_mask(lo, hi)]
            a = auc(e.y, e[q]); a = max(a, 1 - a)
            vals.append(a)
        print(f"{q:12s} " + " ".join(f"{v:8.4f}" for v in vals))

    print("\n## 2. そろい率の記述 — axis_sum 五分位 × 同ライン（確認窓）")
    e = T[_mask(*WINDOWS["確認"][1])].copy()
    e["q"] = pd.qcut(e.axis_sum, 5, labels=False)
    tab = e.groupby(["q", "same_line"]).y.agg(["mean", "size"])
    print(tab.unstack().round(4).to_string())

    res = {}
    for w, ((tlo, thi), (elo, ehi)) in WINDOWS.items():
        tr, te = T[_mask(tlo, thi)], T[_mask(elo, ehi)]
        ytr, yte = tr.y.values.astype(int), te.y.values.astype(int)
        dtr = tr.date.values
        print(f"\n## 3. {w}評価  学習 {len(tr):,}R ({tlo}〜{thi}) → 評価 {len(te):,}R ({elo}〜{ehi})")
        base = fit_logistic(tr[EXIST].values, ytr, te[EXIST].values)
        a_base = auc(yte, base)
        base_g1 = fit_logistic(tr[EXIST + G1].values, ytr, te[EXIST + G1].values)
        print(f"  既存6量 ロジスティック            AUC {a_base:.4f}")
        print(f"  既存6量+G1(確率ベクトル) ロジ     AUC {auc(yte, base_g1):.4f}")
        arms = {
            "LGB 既存6量のみ": EXIST,
            "LGB 既存6量+G1 確率のみ": EXIST + G1,
            "LGB 構造のみ(G2+G3+G4・確率なし)": G2 + G3 + G4,
            "LGB 全部(既存+G1+G2+G3+G4)": ALL,
        }
        res[w] = dict(y=yte, base=base, a_base=a_base, arms={})
        for name, cols in arms.items():
            Xtr, Xte = tr[cols].values, te[cols].values
            preds, stacks, incs, rounds_used = [], [], [], []
            for sd in SEEDS:
                p, r, _ = lgb_pred(Xtr, ytr, dtr, Xte, sd)
                oof = oof_blocks(Xtr, ytr, dtr, sd, r)
                ok = ~np.isnan(oof)
                st = fit_logistic(np.c_[tr[EXIST].values[ok], logit(oof[ok])], ytr[ok],
                                  np.c_[te[EXIST].values, logit(p)])
                preds.append(p); stacks.append(st); rounds_used.append(r)
                incs.append(auc(yte, st) - a_base)
            pm = np.mean(preds, axis=0); sm = np.mean(stacks, axis=0)
            a_single = [auc(yte, p) for p in preds]
            ci = paired_boot(yte, base, sm)
            rho_ax = spearmanr(pm, te.axis_sum).correlation
            rho_pe = spearmanr(pm, te.pw_ent).correlation
            rho_pp = spearmanr(pm, te.p3_prod).correlation
            print(f"  {name:34s} 単体AUC {np.mean(a_single):.4f}±{np.std(a_single):.4f}"
                  f"  既存6量+pred {auc(yte, sm):.4f}  増分 {np.mean(incs):+.4f}"
                  f"±{np.std(incs):.4f} (seed) CI[{ci[0]:+.4f},{ci[1]:+.4f}]"
                  f"  ρ(axis_sum)={rho_ax:+.3f} ρ(pw_ent)={rho_pe:+.3f} ρ(p3_prod)={rho_pp:+.3f}"
                  f"  rounds {rounds_used}")
            res[w]["arms"][name] = dict(pred=pm, stack=sm, inc=float(np.mean(incs)), ci=ci,
                                         a_single=float(np.mean(a_single)))
        # 重要度（全部・seed0）
        _, r, b = lgb_pred(tr[ALL].values, ytr, dtr, te[ALL].values, 0)
        imp = pd.Series(b.feature_importance("gain"), index=ALL).sort_values(ascending=False)
        print("  gain 上位20:", ", ".join(f"{k}({v/imp.sum()*100:.1f}%)" for k, v in imp.head(20).items()))
        grp = {"既存6": EXIST, "G1確率": G1, "G2軸対": G2, "G3集約": G3, "G4": G4}
        print("  群別 gain 比率:", " / ".join(f"{g} {imp[c].sum()/imp.sum()*100:.1f}%" for g, c in grp.items()))

    pickle.dump(res, (HERE / "auc_res.pkl").open("wb"))
    print("\n→", HERE / "auc_res.pkl")


if __name__ == "__main__":
    main()
