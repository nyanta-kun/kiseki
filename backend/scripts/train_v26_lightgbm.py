"""v26 LightGBM 学習スクリプト

v24 のサブ指数（17指数）+ レース・馬メタ情報を特徴量とし、
「3着以内に入る確率」を二値分類で学習する。

- 訓練: 2023-05-01 〜 2025-06-30
- 検証: 2025-07-01 〜 2025-12-31
- テスト: 2026-01-01 〜 2026-04-30

LambdaRank（学習対象=レース内ランキング）も検証する。

出力:
  models/v26_lightgbm.txt   - 学習済みモデル
  models/v26_metrics.json   - 訓練/検証/テストメトリクス
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

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v26_train")

V24_VERSION = 24
MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# 特徴量: v24 サブ指数 17 + レースメタ + 馬メタ
SUBINDEX_FEATURES = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]
RACE_FEATURES = ["distance", "head_count", "is_turf", "is_dirt", "is_jump",
                 "is_good", "is_yaya", "is_heavy", "is_bad", "is_g1g2g3"]
HORSE_FEATURES = ["frame_number", "horse_age", "weight_carried", "horse_weight",
                  "weight_change", "jvan_time_dm", "jvan_battle_dm"]
ALL_FEATURES = SUBINDEX_FEATURES + RACE_FEATURES + HORSE_FEATURES

DATA_QUERY = """
SELECT
    ci.race_id, ci.horse_id,
    -- v24 sub-indices
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    -- race meta
    r.date::int AS race_date,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    -- horse meta
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    rr.weight_change,
    re.jvan_time_dm, re.jvan_battle_dm,
    -- target
    rr.finish_position, rr.win_popularity, rr.win_odds
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = %(ver)s
  AND r.head_count >= 8
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10');
"""


def fetch_dataset(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(DATA_QUERY, {"ver": V24_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"取得: {len(df):,}行 ({start}〜{end})")
    return df


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    """One-hot 化や派生特徴量を生成。"""
    df = df.copy()
    # surface one-hot
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.startswith("芝").astype(int)
    df["is_dirt"] = s.str.startswith("ダ").astype(int)
    df["is_jump"] = s.str.startswith("障").astype(int)
    # condition one-hot
    c = df["condition"].fillna("").astype(str)
    df["is_good"] = (c == "良").astype(int)
    df["is_yaya"] = (c == "稍").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"] = (c == "不").astype(int)
    # grade
    g = df["grade"].fillna("").astype(str)
    df["is_g1g2g3"] = g.str.match(r"^G[1-3]$").astype(int)
    # numeric coercion
    for c in SUBINDEX_FEATURES + HORSE_FEATURES + ["distance", "head_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def evaluate(df_test: pd.DataFrame, scores: np.ndarray, label: str) -> dict:
    """予測スコアでレース内ランキング評価。"""
    df = df_test.copy()
    df["score"] = scores
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")

    # レース内 1位（スコア最大）の馬の的中率
    top1 = df.loc[df.groupby("race_id")["score"].idxmax()]
    win_pct = (top1["finish_position"] == 1).mean() * 100
    place_pct = (top1["finish_position"] <= 3).mean() * 100

    # 単勝 ROI
    top1["win_odds"] = pd.to_numeric(top1["win_odds"], errors="coerce")
    win_returns = ((top1["finish_position"] == 1) * top1["win_odds"]).fillna(0).sum()
    win_roi = win_returns / len(top1)

    metrics = {
        "label": label,
        "n_races": len(top1),
        "n_horses": len(df),
        "top1_win_pct": round(win_pct, 2),
        "top1_place_pct": round(place_pct, 2),
        "top1_win_roi": round(win_roi, 3),
    }
    logger.info(f"[{label}] {metrics}")
    return metrics


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--objective", choices=["binary", "rank"], default="binary",
                   help="binary=3着以内分類, rank=LambdaRank")
    p.add_argument("--num-leaves", type=int, default=63)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--num-iterations", type=int, default=500)
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    df_train = featurize(fetch_dataset(conn, "20230501", "20250630"))
    df_valid = featurize(fetch_dataset(conn, "20250701", "20251231"))
    df_test = featurize(fetch_dataset(conn, "20260101", "20260430"))
    conn.close()

    # ターゲット: 3着以内 (binary)
    df_train["y"] = (df_train["finish_position"] <= 3).astype(int)
    df_valid["y"] = (df_valid["finish_position"] <= 3).astype(int)
    df_test["y"] = (df_test["finish_position"] <= 3).astype(int)

    X_train = df_train[ALL_FEATURES].values
    X_valid = df_valid[ALL_FEATURES].values
    X_test = df_test[ALL_FEATURES].values
    y_train = df_train["y"].values
    y_valid = df_valid["y"].values
    y_test = df_test["y"].values

    if args.objective == "rank":
        # LambdaRank: race_id でグループ化、relevance = 4-finish_position をクリップ
        rel_train = (5 - df_train["finish_position"].fillna(20)).clip(lower=0).astype(int).values
        rel_valid = (5 - df_valid["finish_position"].fillna(20)).clip(lower=0).astype(int).values
        # group sizes
        g_train = df_train.groupby("race_id", sort=False).size().values
        g_valid = df_valid.groupby("race_id", sort=False).size().values
        # データを race_id でソート（重要：group の順序と一致させる）
        df_train = df_train.sort_values("race_id").reset_index(drop=True)
        df_valid = df_valid.sort_values("race_id").reset_index(drop=True)
        X_train = df_train[ALL_FEATURES].values
        X_valid = df_valid[ALL_FEATURES].values
        rel_train = (5 - df_train["finish_position"].fillna(20)).clip(lower=0).astype(int).values
        rel_valid = (5 - df_valid["finish_position"].fillna(20)).clip(lower=0).astype(int).values
        g_train = df_train.groupby("race_id", sort=False).size().values
        g_valid = df_valid.groupby("race_id", sort=False).size().values

        params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [1, 3],
            "num_leaves": args.num_leaves,
            "learning_rate": args.learning_rate,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
        }
        train_set = lgb.Dataset(X_train, rel_train, group=g_train,
                                feature_name=ALL_FEATURES)
        valid_set = lgb.Dataset(X_valid, rel_valid, group=g_valid,
                                feature_name=ALL_FEATURES, reference=train_set)
    else:
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "num_leaves": args.num_leaves,
            "learning_rate": args.learning_rate,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
        }
        train_set = lgb.Dataset(X_train, y_train, feature_name=ALL_FEATURES)
        valid_set = lgb.Dataset(X_valid, y_valid, feature_name=ALL_FEATURES,
                                reference=train_set)

    logger.info(f"学習開始 objective={args.objective} num_leaves={args.num_leaves} "
                f"iter={args.num_iterations}")
    model = lgb.train(
        params,
        train_set,
        num_boost_round=args.num_iterations,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(50)],
    )

    # 評価
    metrics = {}
    s_train = model.predict(X_train, num_iteration=model.best_iteration)
    s_valid = model.predict(X_valid, num_iteration=model.best_iteration)
    s_test = model.predict(X_test, num_iteration=model.best_iteration)
    metrics["train"] = evaluate(df_train, s_train, "train")
    metrics["valid"] = evaluate(df_valid, s_valid, "valid")
    metrics["test"] = evaluate(df_test, s_test, "test")

    # 重要度
    importance = sorted(
        zip(ALL_FEATURES, model.feature_importance(importance_type="gain")),
        key=lambda x: -x[1],
    )
    metrics["feature_importance_top10"] = [
        {"feature": f, "gain": int(g)} for f, g in importance[:10]
    ]

    # 保存
    suffix = args.objective
    model.save_model(str(MODELS_DIR / f"v26_lightgbm_{suffix}.txt"))
    with open(MODELS_DIR / f"v26_metrics_{suffix}.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"完了: model={MODELS_DIR / f'v26_lightgbm_{suffix}.txt'}")
    logger.info(f"重要特徴量 top10:")
    for f, g in importance[:10]:
        logger.info(f"  {f}: {g}")


if __name__ == "__main__":
    main()
