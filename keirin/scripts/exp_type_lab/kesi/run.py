#!/usr/bin/env python3
"""消しモデルの学習と第1段の判定（事前登録 docs/type_lab/kesi_model_2026_10_28.md §2〜§4）。

    .venv/bin/python scripts/exp_type_lab/kesi/run.py /tmp/claude-501/kesi
"""
from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

BASE_FEATS = [
    "race_type", "day_index", "grade", "cup_grade", "distance", "venue_id", "race_no",
    "player_class", "term", "gear_ratio", "style", "race_point", "prediction_mark",
    "s_count", "h_count", "b_count", "front_runner", "stalker", "deep_closer", "marker",
    "first_rate", "second_rate", "third_rate",
    "ex_spurt_pct", "ex_thrust_pct", "ex_left_behind_pct", "ex_split_line_pct", "ex_snatch_pct",
    "rp_rank", "rp_minus_max", "rp_minus_mean",
    "line_size", "line_pos", "is_line_leader", "n_lines", "is_tanki", "line_rp_sum", "line_leader_rp",
    "line_rank_sum", "line_rank_lead", "line_sum_minus_max", "line_lead_minus_max",
    "prev_fin", "fin_mean3", "fin_mean5", "t3_rate3", "t3_rate5", "days_since", "cup_prev_fin", "cup_n_prev",
    "logit_m3", "m3_rank",
]
IV_FEATS = [f"{k}_{c}" for k in ("pre", "post") for c in
            ("condition", "fatigue", "equipment", "confidence", "trouble", "self_eval", "trend", "tactic")]
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_data_in_leaf=200,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
              verbose=-1, seed=20261028, num_threads=8)
M3_MIN, R_MAX, RATIO_MAX, CI_UP_MAX, COVER_MIN = 0.15, 0.70, 0.80, 0.90, 0.05
N_BOOT = 2000


def logit(p):
    p = np.clip(p, 0.005, 0.995)
    return np.log(p / (1 - p))


def fit(df, feats, tr0, tr1, va0):
    """va0〜tr1 で早期停止 → その本数で tr0〜tr1 全体を学習し直す。"""
    tr = df[(df.race_date >= tr0) & (df.race_date <= tr1)]
    a = tr[tr.race_date < va0]; v = tr[tr.race_date >= va0]
    da = lgb.Dataset(a[feats], a.top3, init_score=logit(a.m3), free_raw_data=False)
    dv = lgb.Dataset(v[feats], v.top3, init_score=logit(v.m3), reference=da)
    m = lgb.train(PARAMS, da, 3000, valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])
    n = max(m.best_iteration, 1)
    full = lgb.train(PARAMS, lgb.Dataset(tr[feats], tr.top3, init_score=logit(tr.m3)), n)
    print(f"  学習 {tr0}〜{tr1} {len(tr)}行  早期停止の本数 {n}", flush=True)
    return full, n


def predict(m, d, feats):
    raw = m.predict(d[feats], raw_score=True)
    p = 1 / (1 + np.exp(-(logit(d.m3.values) + raw)))
    return p


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def judge(d, p, label, rng):
    d = d.assign(p=p, r=p / d.m3.clip(0.005, 0.995))
    days = np.array(sorted(d.race_date.unique()))
    di = {x: i for i, x in enumerate(days)}
    # 市場に対する改善（対数損失・レース単位）
    d = d.assign(ll_m=ll(d.top3.values, d.m3.clip(0.005, 0.995).values), ll_p=ll(d.top3.values, d.p.values))
    pr = d.groupby("race_key")[["ll_m", "ll_p"]].sum()
    dd = (pr.ll_p - pr.ll_m).values
    bs = [dd[rng.integers(0, len(dd), len(dd))].mean() for _ in range(N_BOOT)]
    print(f"[{label}] {d.race_key.nunique()}R {len(days)}日  1レースあたり対数損失 市場 {pr.ll_m.mean():.4f} → モデル "
          f"{pr.ll_p.mean():.4f}  Δ{dd.mean():+.4f} [{np.percentile(bs, 2.5):+.4f}, {np.percentile(bs, 97.5):+.4f}]")
    f = d[(d.m3 >= M3_MIN) & (d.r <= R_MAX)]
    cover = f.race_key.nunique() / d.race_key.nunique()
    ratio = f.top3.sum() / f.m3.sum() if len(f) else float("nan")
    hit = np.zeros(len(days)); exp = np.zeros(len(days))
    for x, g in f.groupby("race_date"):
        hit[di[x]] = g.top3.sum(); exp[di[x]] = g.m3.sum()
    b = []
    for _ in range(N_BOOT):
        s = rng.integers(0, len(days), len(days))
        b.append(hit[s].sum() / exp[s].sum() if exp[s].sum() else np.nan)
    lo, hi = np.nanpercentile(b, 2.5), np.nanpercentile(b, 97.5)
    ok1 = ratio <= RATIO_MAX and hi < CI_UP_MAX
    ok2 = cover >= COVER_MIN
    print(f"[{label}] 消し（m3≥{M3_MIN}・r̂≤{R_MAX}）: {len(f)}人 / 平均m3 {f.m3.mean():.3f} / 実際 {f.top3.mean():.3f} / "
          f"実際÷見込み {ratio:.3f} [{lo:.3f}, {hi:.3f}]  レース被覆 {cover * 100:.1f}%  "
          f"→ 判定1 {'通過' if ok1 else '不通過'} / 判定2 {'通過' if ok2 else '不通過'}", flush=True)
    s = d[d.m3 >= M3_MIN].copy()
    s["dec"] = pd.qcut(s.r.rank(method="first"), 10, labels=False)
    t = s.groupby("dec").agg(n=("top3", "size"), r_mean=("r", "mean"), m3=("m3", "mean"), act=("top3", "mean"),
                             ratio=("top3", "sum"))
    t["ratio"] = t["ratio"] / s.groupby("dec")["m3"].sum()
    print(f"[{label}] m3≥{M3_MIN} の r̂ 十分位（実際÷見込み）:")
    print(t.round(3).to_string())
    lo_ = d[d.m3 < M3_MIN].copy()
    if len(lo_):
        lo_["dec"] = pd.qcut(lo_.r.rank(method="first"), 5, labels=False)
        u = lo_.groupby("dec").apply(lambda g: pd.Series(dict(n=len(g), r=g.r.mean(), ratio=g.top3.sum() / g.m3.sum())))
        print(f"[{label}] m3<{M3_MIN} の r̂ 五分位（参考）:")
        print(u.round(3).to_string())
    return ok1, ok2


def main():
    dd = Path(sys.argv[1])
    df = pd.read_pickle(dd / "kesi_feats.pkl")
    df = df[df.race_date >= "2024-07-01"].reset_index(drop=True)
    print("行", len(df), "レース", df.race_key.nunique(), flush=True)
    r0 = df[df.race_date >= "2026-01-01"].race_key.iloc[0]
    print("目視:", df[df.race_key == r0][["race_key", "frame_no", "race_point", "line_pos", "m3", "top3"]].to_string())
    rng = np.random.default_rng(20261028)

    print("== 主モデル", flush=True)
    m, _ = fit(df, BASE_FEATS, "2024-07-01", "2025-12-31", "2025-10-01")
    imp = pd.Series(m.feature_importance("gain"), index=BASE_FEATS).sort_values(ascending=False)
    print("  寄与上位:", ", ".join(f"{k} {v / imp.sum() * 100:.1f}%" for k, v in imp.head(15).items()))
    for lab, a, b in (("確認(主) 2026-01〜05", "2026-01-01", "2026-05-31"),
                      ("確認(副) 2026-06〜10", "2026-06-01", "2026-10-27")):
        d = df[(df.race_date >= a) & (df.race_date <= b)]
        judge(d, predict(m, d, BASE_FEATS), lab, rng)

    print("== コメント版（探索扱い）", flush=True)
    feats = BASE_FEATS + IV_FEATS
    m2, _ = fit(df, feats, "2025-12-01", "2026-03-31", "2026-03-01")
    imp = pd.Series(m2.feature_importance("gain"), index=feats).sort_values(ascending=False)
    print("  寄与上位:", ", ".join(f"{k} {v / imp.sum() * 100:.1f}%" for k, v in imp.head(15).items()))
    d = df[(df.race_date >= "2026-04-01") & (df.race_date <= "2026-05-31")]
    judge(d, predict(m2, d, feats), "コメント版 2026-04〜05", rng)


if __name__ == "__main__":
    main()
