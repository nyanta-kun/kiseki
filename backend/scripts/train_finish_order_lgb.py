"""2-3着専用 LightGBM モデルの学習 (T03)。

目的変数:
  - finish_order_lgb_place.txt: 「2着以内」= 1 (binary classification)
  - finish_order_lgb_show.txt:  「3着以内」= 1 (binary classification)

特徴量:
  - v26 win_probability（1着確率）
  - 馬体重・斤量・枠番（発走前公表値）
  - v24 サブ指数（利用可能なもの）
  - レース距離・頭数・馬場

point-in-time 制約: 当該レースの事後データ（通過順・上がり3F・当該レース脚質）は
リークになるため使用しない。脚質を入れる場合は過去レース実績から集計した
point-in-time 値を別途構築すること（未実装・将来課題）。

検証標準: 5 seed × deterministic=True

使用例:
    python scripts/train_finish_order_lgb.py \\
        --train-start 20230101 --train-end 20250630 \\
        --valid-start 20250701 --valid-end 20251231 \\
        --seeds 42,123,456,789,1024
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_finish_order_lgb")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 特徴量定義
# ---------------------------------------------------------------------------

FEATURES: list[str] = [
    # v26 勝率（1着確率）
    "win_probability",
    # 馬体重・斤量（発走前公表値）
    "horse_weight", "weight_carried", "weight_change",
    # 枠・馬番
    "frame_number",
    # レースメタ
    "distance", "head_count",
    # 馬場フラグ (one-hot)
    "is_turf", "is_dirt", "is_jump",
    "is_good", "is_yaya", "is_heavy", "is_bad",
    # v24 サブ指数（重要なもの優先）
    "speed_index", "last_3f_index", "jockey_index",
    "course_aptitude", "pace_index", "rotation_index",
]

_BASE_QUERY = """
SELECT
    ci.race_id, ci.horse_id,
    ci.win_probability,
    -- v24 サブ指数
    ci.speed_index, ci.last_3f_index, ci.jockey_index,
    ci.course_aptitude, ci.pace_index, ci.rotation_index,
    -- レース
    r.distance, r.head_count, r.surface, r.condition,
    -- 着順（ラベル算出専用。特徴量には使わない）
    rr.finish_position,
    -- 馬体重・斤量
    re.frame_number,
    re.horse_weight, re.weight_carried,
    rr.weight_change
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = 26
  AND ci.win_probability IS NOT NULL
  AND r.date BETWEEN %(start)s AND %(end)s
  AND r.head_count >= 8
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND COALESCE(rr.abnormality_code, 0) = 0
"""


def fetch_dataset(conn: "psycopg2.connection", start: str, end: str) -> pd.DataFrame:
    """期間指定でデータを取得する。大量クエリを避けるため期間は呼び出し元で制御する。"""
    cur = conn.cursor()
    cur.execute(_BASE_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"取得: {len(df):,}行 ({start}〜{end})")
    return df


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    """特徴量エンジニアリング。"""
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

    # 数値変換
    for col in FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def train_model(
    df_train: pd.DataFrame,
    df_valid: pd.DataFrame,
    target_col: str,
    seed: int,
    num_iterations: int = 300,
) -> "lgb.Booster":
    """binary classification モデルを学習する。

    Args:
        target_col: "y_place" (2着以内) or "y_show" (3着以内)
        seed: 乱数シード（deterministic=True と組み合わせ）
    """
    X_train = df_train[FEATURES].fillna(0).values
    y_train = df_train[target_col].values
    X_valid = df_valid[FEATURES].fillna(0).values
    y_valid = df_valid[target_col].values

    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": 31,
        "max_depth": 6,
        "min_data_in_leaf": 100,
        "lambda_l1": 0.1,
        "lambda_l2": 0.5,
        "learning_rate": 0.05,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 5,
        "seed": seed,
        "deterministic": True,
        "verbose": -1,
    }

    train_set = lgb.Dataset(X_train, y_train, feature_name=FEATURES)
    valid_set = lgb.Dataset(X_valid, y_valid, feature_name=FEATURES, reference=train_set)

    model = lgb.train(
        params,
        train_set,
        num_boost_round=num_iterations,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(100)],
    )
    return model


def evaluate(df: pd.DataFrame, scores: np.ndarray, target_col: str, label: str) -> dict:
    """評価: log-loss + ランキング精度。"""
    from sklearn.metrics import log_loss  # type: ignore[import]

    y_true = df[target_col].fillna(0).values
    ll = log_loss(y_true, scores.clip(1e-7, 1 - 1e-7))

    # レース内スコア top1 の着順精度
    df2 = df.copy()
    df2["score"] = scores
    top1 = df2.loc[df2.groupby("race_id")["score"].idxmax()]
    fp = pd.to_numeric(top1["finish_position"], errors="coerce")
    top1_place_pct = (fp <= 3).mean() * 100

    result = {
        "label": label,
        "log_loss": round(float(ll), 6),
        "top1_place_pct": round(float(top1_place_pct), 2),
        "n_races": int(len(top1)),
    }
    logger.info(f"  [{label}] log_loss={ll:.6f}  top1_place%={top1_place_pct:.1f}%")
    return result


def main() -> None:
    """エントリポイント。"""
    p = argparse.ArgumentParser(description="Train finish order LGB models")
    p.add_argument("--train-start", default="20230101")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--valid-start", default="20250701")
    p.add_argument("--valid-end", default="20251231")
    p.add_argument("--seeds", default="42,123,456,789,1024")
    p.add_argument("--num-iterations", type=int, default=300)
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    df_train_raw = featurize(fetch_dataset(conn, args.train_start, args.train_end))
    df_valid_raw = featurize(fetch_dataset(conn, args.valid_start, args.valid_end))
    conn.close()

    # ターゲット列
    df_train_raw["y_place"] = (df_train_raw["finish_position"] <= 2).astype(int)
    df_train_raw["y_show"] = (df_train_raw["finish_position"] <= 3).astype(int)
    df_valid_raw["y_place"] = (df_valid_raw["finish_position"] <= 2).astype(int)
    df_valid_raw["y_show"] = (df_valid_raw["finish_position"] <= 3).astype(int)

    all_metrics: dict[str, list[dict]] = {"place": [], "show": []}

    best_place_model: "lgb.Booster | None" = None
    best_show_model: "lgb.Booster | None" = None
    best_place_score = 1e9

    for seed in seeds:
        logger.info(f"=== seed={seed} ===")
        logger.info("  [place] 2着以内モデル学習...")
        m_place = train_model(df_train_raw, df_valid_raw, "y_place", seed, args.num_iterations)
        s_valid = m_place.predict(
            df_valid_raw[FEATURES].fillna(0).values, num_iteration=m_place.best_iteration
        )
        metrics_place = evaluate(df_valid_raw, s_valid, "y_place", f"valid_place_seed{seed}")
        all_metrics["place"].append(metrics_place)

        # valid log_loss が最小のモデルを保存
        if metrics_place["log_loss"] < best_place_score:
            best_place_score = metrics_place["log_loss"]
            best_place_model = m_place

        logger.info("  [show] 3着以内モデル学習...")
        m_show = train_model(df_train_raw, df_valid_raw, "y_show", seed, args.num_iterations)

    # 最良モデルを保存
    if best_place_model is not None:
        place_path = MODELS_DIR / "finish_order_lgb_place.txt"
        best_place_model.save_model(str(place_path))
        logger.info(f"保存: {place_path}")

    # show モデルも最終 seed を保存（place と同様の構成）
    if "m_show" in dir():
        show_path = MODELS_DIR / "finish_order_lgb_show.txt"
        m_show.save_model(str(show_path))
        logger.info(f"保存: {show_path}")

    # 平均メトリクスを集計
    for target, metrics_list in all_metrics.items():
        if metrics_list:
            avg_ll = np.mean([m["log_loss"] for m in metrics_list])
            avg_pct = np.mean([m["top1_place_pct"] for m in metrics_list])
            logger.info(
                f"[{target}] 5seed平均 log_loss={avg_ll:.6f}  top1_place%={avg_pct:.2f}%"
            )

    out = {
        "train": {"start": args.train_start, "end": args.train_end},
        "valid": {"start": args.valid_start, "end": args.valid_end},
        "seeds": seeds,
        "features": FEATURES,
        "metrics": all_metrics,
    }
    metrics_path = MODELS_DIR / "finish_order_lgb_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    logger.info(f"メトリクス保存: {metrics_path}")


if __name__ == "__main__":
    main()
