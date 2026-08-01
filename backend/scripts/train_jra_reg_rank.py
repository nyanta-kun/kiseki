"""JRA 順位回帰ヘッド（v27 の土台）の本番モデル学習。

目的変数は **レース内正規化着順**（0.0 = 1着 … 1.0 = 最下位）の回帰。
LambdaRank が「上位を当てる」ことに最適化されるのに対し、こちらは
**全順位を並べる**ことを直接の目的関数にする。

検証結果（memory: jra_rank_quality_redesign_2026_08_02）:
  honest test 2026-01〜08 (2,046R) / 独立窓 2025年通年 (3,455R) の両方で
  レース内 Spearman が全候補中トップ（0.5086 / 0.5022）。
  本番 v26 composite（0.3*LGB + 0.7*v24線形和）は 0.4783 / 0.4932 で、
  **LGB 部分単独より劣る**＝線形和 0.7 が足枷になっていた。

特徴量は v26 / 着外率ヘッドと同一の 34 列（`composite.py::OUT_PROB_FEATURE_NAMES`）。
オッズ・人気は使わない。

出力:
  models/jra_reg_rank_lgb.txt      - 本番モデル（全期間 refit）
  models/jra_reg_rank_metrics.json - honest test メトリクス

使い方:
    cd backend
    .venv/bin/python scripts/train_jra_reg_rank.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.train_jra_out_rate import featurize, load_df  # noqa: E402
from src.indices.composite import OUT_PROB_FEATURE_NAMES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_jra_reg_rank")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODELS_DIR / "jra_reg_rank_lgb.txt"
METRICS_PATH = MODELS_DIR / "jra_reg_rank_metrics.json"

FEATURES = OUT_PROB_FEATURE_NAMES


def _params(seed: int) -> dict:
    return dict(
        objective="regression", metric="l2",
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )


def normalized_rank(df: pd.DataFrame) -> np.ndarray:
    """レース内正規化着順（0=1着, 1=最下位）を返す。"""
    r = df.groupby("race_id")["finish_position"].rank(method="min")
    n = df.groupby("race_id")["finish_position"].transform("size")
    return ((r - 1) / (n - 1).clip(lower=1)).values


def spearman_by_race(df: pd.DataFrame, score: np.ndarray) -> float:
    """レース内 Spearman ρ の平均（score は小さいほど上位）。"""
    from scipy.stats import spearmanr
    d = df.copy()
    d["_s"] = score
    vals = []
    for _, g in d.groupby("race_id"):
        if len(g) < 3:
            continue
        rho = spearmanr(g["_s"], g["finish_position"]).correlation
        if not np.isnan(rho):
            vals.append(rho)
    return float(np.mean(vals)) if vals else float("nan")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20991231")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--valid-end", default="20251231")
    p.add_argument("--seeds", default="42,123,456")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    df = featurize(load_df(args.start, args.end))
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    df["y"] = normalized_rank(df)
    logger.info(f"学習データ: {len(df):,}行 / {df['race_id'].nunique():,}レース "
                f"({df['date'].min()}〜{df['date'].max()})")

    tr = df[df["date"] <= args.train_end]
    va = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end]
    logger.info(f"train={len(tr):,} valid={len(va):,} test={len(te):,}")

    best_iters, te_preds = [], []
    for seed in seeds:
        d = lgb.Dataset(tr[FEATURES].values, label=tr["y"].values, feature_name=FEATURES)
        dv = lgb.Dataset(va[FEATURES].values, label=va["y"].values, reference=d)
        m = lgb.train(_params(seed), d, num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        best_iters.append(m.best_iteration)
        if len(te):
            te_preds.append(m.predict(te[FEATURES].values, num_iteration=m.best_iteration))
    n_rounds = int(np.median(best_iters))
    logger.info(f"best_iter={best_iters} → refit rounds={n_rounds}")

    metrics: dict = {
        "objective": "regression on race-normalized finish rank (0=1st, 1=last)",
        "train_period": [df["date"].min(), args.train_end],
        "valid_period": [args.train_end, args.valid_end],
        "seeds": seeds, "best_iters": best_iters, "refit_rounds": n_rounds,
        "features": FEATURES,
    }
    if len(te):
        pte = np.mean(te_preds, axis=0)
        rho = spearman_by_race(te, pte)
        rmse = float(np.sqrt(np.mean((pte - te["y"].values) ** 2)))
        metrics["test"] = {
            "period": [te["date"].min(), te["date"].max()],
            "n": len(te), "n_races": int(te["race_id"].nunique()),
            "spearman_by_race": round(rho, 5), "rmse": round(rmse, 5),
        }
        logger.info(f"honest test: spearman={rho:.4f} rmse={rmse:.4f}")

    dall = lgb.Dataset(df[FEATURES].values, label=df["y"].values, feature_name=FEATURES)
    final = lgb.train(_params(seeds[0]), dall, num_boost_round=n_rounds)
    final.save_model(str(MODEL_PATH))
    metrics["model_path"] = str(MODEL_PATH)
    metrics["refit_period"] = [df["date"].min(), df["date"].max()]
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    logger.info(f"保存: {MODEL_PATH} / {METRICS_PATH}")


if __name__ == "__main__":
    main()
