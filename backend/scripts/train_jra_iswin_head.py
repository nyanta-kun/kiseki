"""JRA is_win 較正ヘッド 学習スクリプト（本番 win_probability 較正用）

jra_calibration_ab.py の検証で、softmax(composite) は未較正(OOS ECE 0.033・最上位
decile +16pt 過信)、is_win binary LGB の生出力＋レース内正規化が OOS ECE 0.0027 と
ほぼ完璧に較正されると判明。本スクリプトはその is_win ヘッドを本番モデルとして学習・
保存する。composite.py が推論時にレース内正規化して win_probability に使う。

- 特徴量: composite._build_v26_features と同一(v24サブ17 + レースメタ10 + 馬メタ7 = 34)
- 目的: binary is_win (1着=1)
- 本番モデル: 全期間 seed0 で学習(データ最大化)。OOS sanity を併記。

出力:
  models/v26_iswin_calib.txt    - 較正ヘッド
  models/v26_iswin_calib_metrics.json

使い方:
  cd backend
  .venv/bin/python scripts/train_jra_iswin_head.py
"""
from __future__ import annotations

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
import psycopg2  # noqa: E402

from scripts.jra_calibration_ab import (  # noqa: E402
    ALL_FEATURES,
    QUERY,
    V26_VERSION,
    calib_metrics,
    featurize,
    race_normalize,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_iswin")

MODELS_DIR = _root / "models"
MODEL_PATH = MODELS_DIR / "v26_iswin_calib.txt"
METRICS_PATH = MODELS_DIR / "v26_iswin_calib_metrics.json"

PARAMS = dict(
    objective="binary", metric="binary_logloss", num_leaves=31, max_depth=6,
    min_data_in_leaf=100, lambda_l1=0.1, lambda_l2=0.1, learning_rate=0.05,
    feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=5, verbose=-1,
)
NUM_ROUND = 500


def fetch(conn, start, end):
    import pandas as pd
    cur = conn.cursor()
    cur.execute(QUERY, {"ver": V26_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    df["date"] = df["date"].astype(str)
    return featurize(df)


def train_model(df, seed=0):
    X = df[ALL_FEATURES].values.astype(float)
    y = (df["finish_position"] == 1).astype(int).values
    ds = lgb.Dataset(X, y, feature_name=ALL_FEATURES)
    return lgb.train(dict(PARAMS, seed=seed), ds, num_boost_round=NUM_ROUND)


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20260605")
    p.add_argument("--oos-train-end", default="20250630")
    p.add_argument("--oos-test-start", default="20250701")
    args = p.parse_args()

    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch(conn, args.start, args.end)
    conn.close()
    df = df[df["finish_position"].notna()].reset_index(drop=True)
    logger.info("全データ: %d行 %dレース", len(df), df["race_id"].nunique())

    # ── OOS sanity（過学習チェック） ──
    tr = df[df["date"] <= args.oos_train_end]
    te = df[df["date"] >= args.oos_test_start].reset_index(drop=True)
    m_oos = train_model(tr, seed=0)
    raw_te = np.asarray(m_oos.predict(te[ALL_FEATURES].values.astype(float)), dtype=float)
    norm_te = race_normalize(raw_te, te["race_id"])
    y_te = (te["finish_position"] == 1).astype(int).values
    cm_raw = calib_metrics(raw_te, y_te)
    cm_norm = calib_metrics(norm_te, y_te)
    cm_softmax = calib_metrics(te["softmax_win"].values, y_te)
    logger.info("OOS sanity (test %s〜): softmax ECE=%.4f / iswin raw ECE=%.4f / iswin norm ECE=%.4f",
                args.oos_test_start, cm_softmax["ece"], cm_raw["ece"], cm_norm["ece"])

    # ── 本番モデル: 全期間 seed0 ──
    model = train_model(df, seed=0)
    MODELS_DIR.mkdir(exist_ok=True)
    model.save_model(str(MODEL_PATH))
    metrics = {
        "trained_on": f"{args.start}-{args.end}",
        "n_rows": int(len(df)),
        "n_races": int(df["race_id"].nunique()),
        "features": ALL_FEATURES,
        "params": PARAMS,
        "num_round": NUM_ROUND,
        "oos_sanity": {
            "test_start": args.oos_test_start,
            "softmax_ece": round(cm_softmax["ece"], 4),
            "iswin_raw_ece": round(cm_raw["ece"], 4),
            "iswin_norm_ece": round(cm_norm["ece"], 4),
            "softmax_brier": round(cm_softmax["brier"], 4),
            "iswin_norm_brier": round(cm_norm["brier"], 4),
        },
    }
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    logger.info("保存完了: %s", MODEL_PATH)
    logger.info("metrics: %s", json.dumps(metrics["oos_sanity"], ensure_ascii=False))


if __name__ == "__main__":
    main()
