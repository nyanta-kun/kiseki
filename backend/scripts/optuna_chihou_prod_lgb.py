"""地方本番モデル(44特徴 binary is_top3)の Optuna ハイパラ探索 + 最終A/B。

現行 PARAMS は手置き(num_leaves=31/depth=5/lr=0.05)で一度もチューニングされていない。

■ リーク防止プロトコル
  tune : train 20230101〜20250228 / valid 20250301〜20250630 (logloss, early stopping)
  final: 標準2cutoff (20250630 / 20251231) × 5seed で 現行params vs best params を
         top1勝率(is_win)/複勝率(is_top3) で A/B。valid は両テスト期間と重複しない。

■ 判定基準（確立済み検証規律と同一）
  top1勝率/複勝率が両cutoffで全seed改善かつ Δ>std → 採用

使い方:
  cd backend
  PYTHONPATH=. .venv/bin/python scripts/optuna_chihou_prod_lgb.py --trials 50
  PYTHONPATH=. .venv/bin/python scripts/optuna_chihou_prod_lgb.py --skip-tune \
      --params-json /tmp/optuna_best.json   # 探索済み params で final A/B のみ
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.train_chihou_market_lgb import (  # noqa: E402
    ALL_FEATURES,
    fetch,
    prep,
)
from scripts.train_chihou_prod_lgb import PARAMS as CURRENT_PARAMS  # noqa: E402
from scripts.train_chihou_prod_lgb import fetch_hist  # noqa: E402

SEEDS = [0, 1, 2, 3, 4]
TUNE_TRAIN = ("20230101", "20250228")
TUNE_VALID = ("20250301", "20250630")
FINAL_CUTOFFS = [
    ("20250630", "20250701", "20251231"),
    ("20251231", "20260101", "20260706"),
]
DATA_START, DATA_END = "20230101", "20260706"


def _conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))


def _slim(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["race_id", "date", "finish_position", "win_odds"] + list(ALL_FEATURES)
    out = df[cols].copy()
    for c in cols:
        if c not in ("race_id", "date"):
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float32")
    return out


def _eval_top1(te: pd.DataFrame, win_score, top3_score) -> dict:
    d = te.copy()
    d["sw"] = win_score
    d["s3"] = top3_score
    r1w = d.loc[d.groupby("race_id")["sw"].idxmax()]
    r1p = d.loc[d.groupby("race_id")["s3"].idxmax()]
    return {
        "win_pct": float((r1w["finish_position"] == 1).mean() * 100),
        "place_pct": float((r1p["finish_position"] <= 3).mean() * 100),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=50)
    p.add_argument("--skip-tune", action="store_true")
    p.add_argument("--params-json", default="/tmp/optuna_chihou_best.json")
    args = p.parse_args()

    conn = _conn()
    df_hist = fetch_hist(conn)
    print("全期間データ取得中...", flush=True)
    df_all = _slim(prep(conn, fetch(conn, DATA_START, DATA_END), df_hist))
    conn.close()
    gc.collect()
    print(f"{df_all['race_id'].nunique()}R / {len(df_all)}行", flush=True)

    fp = df_all["finish_position"]
    df_all["y_top3"] = (fp <= 3).astype(int)
    df_all["y_win"] = (fp == 1).astype(int)

    # ── Optuna 探索（is_top3 head, valid logloss）──
    if not args.skip_tune:
        import optuna

        tr = df_all[(df_all["date"] >= TUNE_TRAIN[0]) & (df_all["date"] <= TUNE_TRAIN[1])]
        va = df_all[(df_all["date"] >= TUNE_VALID[0]) & (df_all["date"] <= TUNE_VALID[1])]
        Xtr = tr[ALL_FEATURES].to_numpy(np.float32)
        Xva = va[ALL_FEATURES].to_numpy(np.float32)
        ytr, yva = tr["y_top3"].to_numpy(), va["y_top3"].to_numpy()
        # min_data_in_leaf を trial 毎に変えるため pre-filter を無効化（Dataset再利用の定番対処）
        dtr = lgb.Dataset(Xtr, ytr, feature_name=list(ALL_FEATURES),
                          params={"feature_pre_filter": False}, free_raw_data=False)
        dva = lgb.Dataset(Xva, yva, reference=dtr)
        print(f"tune: train {tr['race_id'].nunique()}R / valid {va['race_id'].nunique()}R", flush=True)

        def objective(trial: "optuna.Trial") -> float:
            params = {
                "objective": "binary", "metric": "binary_logloss", "verbose": -1,
                "deterministic": True, "force_row_wise": True, "seed": 0,
                "num_leaves": trial.suggest_int("num_leaves", 15, 127),
                "max_depth": trial.suggest_int("max_depth", 4, 9),
                "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 20, 200),
                "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 10.0, log=True),
                "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
                "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.1, log=True),
                "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
                "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
                "bagging_freq": trial.suggest_int("bagging_freq", 1, 10),
            }
            m = lgb.train(params, dtr, num_boost_round=1500, valid_sets=[dva],
                          callbacks=[lgb.early_stopping(50, verbose=False)])
            trial.set_user_attr("best_iter", m.best_iteration)
            return m.best_score["valid_0"]["binary_logloss"]

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize",
                                    sampler=optuna.samplers.TPESampler(seed=0))
        study.optimize(objective, n_trials=args.trials, show_progress_bar=False)
        best = dict(study.best_params)
        best["num_boost_round"] = int(study.best_trial.user_attrs["best_iter"])
        print(f"\nbest logloss={study.best_value:.5f} params={best}", flush=True)
        Path(args.params_json).write_text(json.dumps(best, indent=2))
    else:
        best = json.loads(Path(args.params_json).read_text())
        print(f"params-json 読込: {best}", flush=True)

    # ── final A/B: 現行 PARAMS vs best（5seed × 2cutoff）──
    n_rounds_best = best.pop("num_boost_round", 400)
    base_best = {
        "objective": "binary", "metric": "binary_logloss", "verbose": -1,
        "deterministic": True, "force_row_wise": True, **best,
    }
    base_cur = dict(CURRENT_PARAMS, deterministic=True, force_row_wise=True)

    for cutoff, ts, te_ in FINAL_CUTOFFS:
        tr = df_all[df_all["date"] <= cutoff]
        te = df_all[(df_all["date"] >= ts) & (df_all["date"] <= te_)]
        Xtr = tr[ALL_FEATURES].to_numpy(np.float32)
        Xte = te[ALL_FEATURES].to_numpy(np.float32)
        res: dict[str, list[dict]] = {"current": [], "tuned": []}
        for seed in SEEDS:
            for name, bp, nr in (("current", base_cur, 400), ("tuned", base_best, n_rounds_best)):
                params = dict(bp, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
                mw = lgb.train(params, lgb.Dataset(Xtr, tr["y_win"].to_numpy(),
                               feature_name=list(ALL_FEATURES)), num_boost_round=nr)
                m3 = lgb.train(params, lgb.Dataset(Xtr, tr["y_top3"].to_numpy(),
                               feature_name=list(ALL_FEATURES)), num_boost_round=nr)
                res[name].append(_eval_top1(te, mw.predict(Xte), m3.predict(Xte)))
            print(f"  cutoff {cutoff} seed {seed} 完了", flush=True)
        print(f"\n--- cutoff {cutoff} (test {ts}〜{te_}, {te['race_id'].nunique()}R) ---")
        for label, key in (("top1勝率%", "win_pct"), ("top1複勝率%", "place_pct")):
            b = np.array([r[key] for r in res["current"]])
            a = np.array([r[key] for r in res["tuned"]])
            d = a.mean() - b.mean()
            nb = sum(1 for i in range(len(SEEDS)) if a[i] > b[i])
            sig = "★std超" if d > max(b.std(), a.std()) else ("◯" if d > 0 else "✗")
            print(f"  {label:12} current {b.mean():.3f}±{b.std():.3f} → tuned {a.mean():.3f}±{a.std():.3f}"
                  f" | Δ={d:+.3f} ({nb}/{len(SEEDS)}seed改善) {sig}")
        del Xtr, Xte
        gc.collect()

    print("\n判定基準: 両cutoffで top1勝率/複勝率 全seed改善かつΔ>std → params 採用。")


if __name__ == "__main__":
    main()
