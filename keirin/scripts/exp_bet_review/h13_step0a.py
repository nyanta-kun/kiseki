#!/usr/bin/env python3
"""H13 Step0a: B（主）/ S（副）を取る選手を、新モデル vs 素朴(b_rate_90 正規化) vs 現行隊列推定 で比べる。

  学習: 2024-01-01〜12-31（5〜9車・B がちょうど1人のレース）。早期停止は 2024-11〜12 を検証にし、
        best_iter を決めたら 2024 全体で同じ反復数 refit（2025 は一切見ない・ハイパラは固定で掃引しない）。
  評価: 2025 の7車（race_type_board.npz の KEY・B がちょうど1人）。レース内で合計1に正規化。
  同率の top1 は車番最小。CI = 開催日ブートストラップ 2,000 回・95%・seed 固定。
  出力: data/exp_bet_review/h13/step0a.json, pred_2025.pkl, models/*.txt
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import lightgbm as lgb
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from h13_features import FEATURES, D

SEED, NB = 20261005, 2000
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=100, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, seed=SEED, num_threads=4)
EPS = 1e-6


def exactly_one(f, col):
    s = f.groupby("race_key")[col].agg(["sum", "count", "size"])
    return set(s.index[(s["sum"] == 1) & (s["count"] == s["size"])])


def fit(tr, va, tgt):
    dt = lgb.Dataset(tr[FEATURES], tr[tgt]); dv = lgb.Dataset(va[FEATURES], va[tgt])
    m = lgb.train(PARAMS, dt, 1500, valid_sets=[dv], callbacks=[lgb.early_stopping(50, verbose=False)])
    bi = max(m.best_iteration, 20)
    full = pd.concat([tr, va])
    m2 = lgb.train(PARAMS, lgb.Dataset(full[FEATURES], full[tgt]), int(round(bi * 1.1)))
    return m2, bi


def norm(df, col):
    return df[col] / df.groupby("race_key")[col].transform("sum")


def race_metrics(df, pcol, tgt):
    """レースごとの (top1 的中, 正解の対数損失)。同率は車番最小。"""
    out = {}
    for k, s in df.sort_values(["race_key", "frame_no"]).groupby("race_key", sort=False):
        p = s[pcol].values; y = s[tgt].values
        out[k] = (float(y[int(np.argmax(p))] == 1), float(-np.log(max(p[y == 1][0], EPS))))
    return out


def fit_naive(tr, rate):
    """素朴の較正版（参考）: p ∝ (rate+eps)^g を 2024 学習窓の logloss 最小で (eps,g) 格子探索。"""
    best = None
    grp = [(s[rate].values, s["_y"].values) for _, s in tr.groupby("race_key")]
    for eps in (0.001, 0.003, 0.01, 0.03, 0.1):
        for g in (0.5, 0.75, 1.0, 1.5, 2.0):
            ll = 0.0
            for r, y in grp:
                w = (r + eps) ** g; ll -= np.log(w[y == 1][0] / w.sum())
            ll /= len(grp)
            if best is None or ll < best[0]:
                best = (ll, eps, g)
    return best


def boot(days, diff, nb=NB, seed=SEED):
    """diff: レース別の差。開催日ブートストラップの (点推定, lo, hi)。"""
    ud, inv = np.unique(days, return_inverse=True)
    sm = np.bincount(inv, weights=diff, minlength=len(ud)); ct = np.bincount(inv, minlength=len(ud)).astype(float)
    rng = np.random.default_rng(seed)
    ix = rng.integers(0, len(ud), (nb, len(ud)))
    bs = sm[ix].sum(1) / ct[ix].sum(1)
    return float(sm.sum() / ct.sum()), float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))


def one_target(f, K, tgt, rate, label):
    ok = exactly_one(f, tgt)
    f = f.copy(); f["_y"] = f[tgt]
    tr = f[(f.race_date <= "2024-10-31") & f.race_key.isin(ok) & f.n_start.between(5, 9)]
    va = f[(f.race_date >= "2024-11-01") & (f.race_date <= "2024-12-31") & f.race_key.isin(ok) & f.n_start.between(5, 9)]
    te = f[f.race_key.isin(K) & f.race_key.isin(ok)].copy()
    m, bi = fit(tr, va, tgt)
    m.save_model(str(D / f"{label}_lgb.txt"))
    te["p_new"] = m.predict(te[FEATURES]); te["p_new"] = norm(te, "p_new")
    trall = pd.concat([tr, va])
    out = {"label": label, "best_iter": bi, "n_train_races": int(trall.race_key.nunique()), "n_test_races": int(te.race_key.nunique())}
    # 素朴（eps=0.01, g=1）＋感度＋較正版
    for eps in (0.001, 0.01, 0.02):
        te[f"w{eps}"] = te[rate] + eps; te[f"p_naive{eps}"] = norm(te, f"w{eps}")
    ll_c, eps_c, g_c = fit_naive(trall, rate)
    te["w_cal"] = (te[rate] + eps_c) ** g_c; te["p_naive_cal"] = norm(te, "w_cal")
    out["naive_cal_params"] = dict(eps=eps_c, g=g_c, train_ll=ll_c)
    rm = {c: race_metrics(te, c, tgt) for c in ["p_new", "p_naive0.01", "p_naive0.001", "p_naive0.02", "p_naive_cal"]}
    keys = sorted(rm["p_new"]); dt = te.drop_duplicates("race_key").set_index("race_key").loc[keys]
    days = dt["race_date"].values; half = (dt["race_date"].values >= "2025-07-01").astype(int)
    A = {c: np.array([rm[c][k] for k in keys]) for c in rm}
    res = {}
    if label == "B":
        f_lead = te.loc[te.groupby("race_key")["formation_pos_frac"].idxmin()].set_index("race_key").loc[keys, tgt].values
        A["formation"] = np.column_stack([f_lead, np.full(len(keys), np.nan)])
        te["is_form_lead"] = (te["formation_pos_frac"] == te.groupby("race_key")["formation_pos_frac"].transform("min")).astype(int)
    for hname, hm in (("all", np.ones(len(keys), bool)), ("H1", half == 0), ("H2", half == 1)):
        for c in A:
            res[f"{hname}|{c}|hit"] = dict(n=int(hm.sum()), val=float(A[c][hm, 0].mean()))
            if c != "formation":
                res[f"{hname}|{c}|ll"] = dict(n=int(hm.sum()), val=float(A[c][hm, 1].mean()))
        base = ["p_naive0.01", "p_naive0.001", "p_naive0.02", "p_naive_cal"] + (["formation"] if label == "B" else [])
        for c in base:
            d_hit = A["p_new"][hm, 0] - A[c][hm, 0]
            res[f"{hname}|new-{c}|hit"] = dict(zip(("diff", "lo", "hi"), boot(days[hm], d_hit)))
            if c != "formation":
                d_ll = A[c][hm, 1] - A["p_new"][hm, 1]       # 正 = 新が良い
                res[f"{hname}|{c}-new|ll"] = dict(zip(("diff", "lo", "hi"), boot(days[hm], d_ll)))
    out["res"] = res
    imp = pd.Series(m.feature_importance("gain"), index=FEATURES).sort_values(ascending=False)
    out["importance_top10"] = (imp / imp.sum()).head(10).round(4).to_dict()
    return out, te, rm


def main():
    f = pd.read_pickle(D / "feat.pkl")
    z = np.load(D.parent / "race_type_board.npz", allow_pickle=True)
    m = (z["DATE"] >= "2025-01-01") & (z["DATE"] <= "2025-12-31"); K = set(map(str, z["KEY"][m]))
    res = {}
    n_all = len(K)
    ok_b = exactly_one(f[f.race_key.isin(K)], "res_back"); ok_s = exactly_one(f[f.race_key.isin(K)], "res_standing")
    res["pop"] = dict(board_2025_7car=n_all, with_one_B=len(ok_b), with_one_S=len(ok_s))
    rb, teb, rmb = one_target(f, K, "res_back", "b_rate_90", "B")
    rs, tes, rms = one_target(f, K, "res_standing", "s_rate_90", "S")
    res["B"], res["S"] = rb, rs
    json.dump(res, open(D / "step0a.json", "w"), indent=1, default=float)
    teb.to_pickle(D / "pred_2025.pkl")
    # 目視用: 1 レースの予測
    k = sorted(rmb["p_new"])[300]
    print(teb[teb.race_key == k][["race_key", "frame_no", "style", "line_group", "line_pos", "b_rate_90", "p_new", "p_naive0.01", "formation_pos_frac", "res_back"]].to_string())
    print(json.dumps({k: v for k, v in res["pop"].items()})); print("done")


if __name__ == "__main__":
    main()
