"""地方競馬 本番用 LightGBM 学習スクリプト（21特徴量・純LGB）

本番リアルタイム取込パス（chihou_calculator.calculate_and_save）が読み込む
プロダクションモデルを学習・保存する。Phase3 で履歴系4特徴
(improving_form/track_win_rate/class_drop_ratio/prev_pace_ratio)を追加。
これらは calculate_and_save 内の _history_features_batch でライブ計算され、
本スクリプトの add_historical_features と同一意味論で算出する（train/serve 整合）。

特徴量(21): サブ指数5 + レースメタ7 + 馬メタ5 + 履歴系4(Phase3)
2ヘッド構成（Phase2: 単複ヘッド分離＋確率較正）:
  - is_top3 ヘッド → composite ランキング & place_probability  (models/chihou_prod_lgb.txt)
  - is_win  ヘッド → win_probability(較正済)                   (models/chihou_prod_lgb_win.txt)
  生 binary 出力がほぼ完璧に較正される(Phase2: win ECE 0.0024)ため isotonic は不要。

クリーンOOS検証は scripts/chihou_model_compare.py 側で実施済み
(LGB17特徴 top1勝率 33.9% vs linear 29.9% / market 46.1%)。
本スクリプトは出荷用に全期間で学習する（将来は月次再学習で更新）。

使い方:
  cd backend
  .venv/bin/python scripts/train_chihou_prod_lgb.py
  .venv/bin/python scripts/train_chihou_prod_lgb.py --start 20230101 --end 20260605 --oos-check
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
logger = logging.getLogger("chihou_prod_train")

CHIHOU_V9_VERSION = 9
MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# 17特徴量（chihou_calculator._build_lgb_features と完全一致させること）
FEATURES = [
    "speed_index", "last3f_index", "jockey_index", "rotation_index", "last_margin_index",
    "distance", "head_count", "is_turf", "is_dirt", "is_good", "is_heavy", "is_bad",
    "frame_number", "horse_age", "weight_carried", "horse_weight", "weight_change",
]
# Phase3: 履歴系4特徴を追加（calculator._history_features_batch と同一意味論）
HIST_FEATURES = ["improving_form", "track_win_rate", "class_drop_ratio", "prev_pace_ratio"]
# Phase4(2026-06-07): 外部指数(kichiuma sp_score / netkeiba idx_ave)を入力特徴に統合。
# 5seed×2cutoff OOS A/B で top1勝率+0.8〜1.3pt / 複勝率+1.6〜2.4pt 改善（scripts/ab_chihou_external_features.py）。
# 発走前公表値=リークなし。レース内z・順位/頭数・欠損フラグ。chihou_calculator._build_lgb_features と同順。
EXT_FEATURES = ["kc_sp_z", "nk_idx_z", "kc_rank_n", "nk_rank_n", "ext_missing"]
FEATURES = FEATURES + HIST_FEATURES + EXT_FEATURES

# train_chihou_v11 の履歴特徴計算を再利用（serve 側 _history_features_batch と整合）
from scripts.train_chihou_v11_lightgbm import (  # noqa: E402
    add_historical_features,
    fetch_hist,
)

BASE_QUERY = """
SELECT
    ci.race_id, r.date, r.course_name, r.prize_1st AS curr_prize,
    re.horse_id, r.surface, r.condition, r.distance, r.head_count,
    re.frame_number, re.horse_age, re.weight_carried,
    COALESCE(re.horse_weight, 500) AS horse_weight,
    COALESCE(re.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    rr.finish_position, rr.win_odds,
    -- Phase4: 外部指数(発走前公表値)。netkeiba idx_ave は '*' を除去して数値化・is_time_index=true 限定。
    CASE WHEN nk.idx_ave ~ '^-?[0-9]+\\*?$'
         THEN regexp_replace(nk.idx_ave, '\\*', '')::float ELSE NULL END AS nk_idx,
    kc.sp_score AS kc_sp
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
LEFT JOIN sekito.racecourse rc ON rc.netkeiba_id = r.course
LEFT JOIN sekito.netkeiba nk
  ON nk.course_code = rc.code AND nk.date = to_date(r.date, 'YYYYMMDD')
     AND nk.race_no = r.race_number AND nk.horse_no = re.horse_number
     AND nk.is_time_index = true
LEFT JOIN sekito.kichiuma kc
  ON kc.course_code = rc.code AND kc.date = to_date(r.date, 'YYYYMMDD')
     AND kc.race_no = r.race_number AND kc.horse_no = re.horse_number
WHERE ci.version = %(ver)s
  AND r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY r.date, ci.race_id
"""

PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "max_depth": 5,
    "min_data_in_leaf": 50,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "seed": 0,
    "verbose": -1,
}
NUM_ROUNDS = 400


def fetch(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.contains("芝").astype(int)
    df["is_dirt"] = s.str.contains("ダ").astype(int)
    c = df["condition"].fillna("").astype(str)
    df["is_good"] = (c == "良").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"] = (c == "不").astype(int)
    for col in FEATURES:
        if col in df.columns:  # 履歴系は add_historical_features 後に付与
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def add_external_features(df: pd.DataFrame) -> pd.DataFrame:
    """Phase4: 外部指数(kichiuma sp_score / netkeiba idx_ave)をレース内正規化特徴に変換。

    chihou_calculator._build_lgb_features と完全一致させること（train/serve parity）:
      kc_sp_z / nk_idx_z : レース内z (欠損→0)
      kc_rank_n / nk_rank_n: レース内順位(降順min)/頭数 (0=最良, 欠損→0.5)
      ext_missing: 両外部指数欠損フラグ
    """
    df = df.copy()
    df["nk_idx"] = pd.to_numeric(df.get("nk_idx"), errors="coerce")
    df["kc_sp"] = pd.to_numeric(df.get("kc_sp"), errors="coerce")

    def zscore(s: pd.Series) -> pd.Series:
        sd = s.std()
        return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0

    g = df.groupby("race_id")
    df["kc_sp_z"] = g["kc_sp"].transform(zscore).fillna(0.0)
    df["nk_idx_z"] = g["nk_idx"].transform(zscore).fillna(0.0)
    hc = pd.to_numeric(df["head_count"], errors="coerce").clip(lower=1)
    df["kc_rank_n"] = ((g["kc_sp"].rank(ascending=False, method="min") - 1) / hc).fillna(0.5)
    df["nk_rank_n"] = ((g["nk_idx"].rank(ascending=False, method="min") - 1) / hc).fillna(0.5)
    df["ext_missing"] = (df["kc_sp"].isna() & df["nk_idx"].isna()).astype(int)
    return df


def prep(conn, df_raw: pd.DataFrame, df_hist: pd.DataFrame) -> pd.DataFrame:
    """featurize + 履歴系4特徴付与 + 外部指数特徴 + 欠損補完（train/serve 整合）。"""
    df = featurize(df_raw)
    df = add_historical_features(df, df_hist)
    for col in HIST_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1.0)
    df = add_external_features(df)
    return df


def _eval_top1(df: pd.DataFrame, scores: np.ndarray, label: str) -> dict:
    d = df.copy()
    d["score"] = scores
    d["fp"] = pd.to_numeric(d["finish_position"], errors="coerce")
    t1 = d.loc[d.groupby("race_id")["score"].idxmax()]
    win = (t1["fp"] == 1).mean() * 100
    place = (t1["fp"] <= 3).mean() * 100
    logger.info("[%s] top1勝率 %.1f%% / 複勝 %.1f%% (n_race=%d)", label, win, place, len(t1))
    return {"label": label, "top1_win_pct": round(win, 2), "top1_place_pct": round(place, 2), "n_races": len(t1)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230101")
    p.add_argument("--end", default="20260605")
    p.add_argument("--oos-check", action="store_true", help="出荷前に時系列OOSで sanity check")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    df_hist = fetch_hist(conn)  # 履歴特徴用（全期間 race_results）

    if args.oos_check:
        # train前半 / test後半 で sanity（出荷モデルとは別）
        cut = "20250630"
        tr = prep(conn, fetch(conn, args.start, cut), df_hist)
        te = prep(conn, fetch(conn, "20250701", args.end), df_hist)
        ytr = (pd.to_numeric(tr["finish_position"], errors="coerce") <= 3).astype(int).values
        m = lgb.train(PARAMS, lgb.Dataset(tr[FEATURES].values.astype(float), ytr, feature_name=FEATURES),
                      num_boost_round=NUM_ROUNDS)
        _eval_top1(te, m.predict(te[FEATURES].values.astype(float)), "OOS-check(test 2025.7+)")

    # ── 出荷モデル: 全期間で学習（単勝/複勝 2ヘッド） ──
    df = prep(conn, fetch(conn, args.start, args.end), df_hist)
    conn.close()
    logger.info("学習データ: %d行 %dレース (%s〜%s)", len(df), df["race_id"].nunique(), args.start, args.end)

    X = df[FEATURES].values.astype(float)
    fp = pd.to_numeric(df["finish_position"], errors="coerce")
    # composite ランキング & place_probability 用: 複勝(3着以内)ヘッド
    # win_probability(較正) 用: 単勝(1着)ヘッド。is_win 生出力はほぼ完璧に較正される
    # (Phase2 実験: ECE 0.0024) ため isotonic は不要。
    heads = [
        ("chihou_prod_lgb",      "is_top3", (fp <= 3).astype(int).values),
        ("chihou_prod_lgb_win",  "is_win",  (fp == 1).astype(int).values),
    ]
    for out_name, label, y in heads:
        model = lgb.train(PARAMS, lgb.Dataset(X, y, feature_name=FEATURES), num_boost_round=NUM_ROUNDS)
        _eval_top1(df, model.predict(X), f"train in-sample[{label}]")
        model.save_model(str(MODELS_DIR / f"{out_name}.txt"))
        importance = sorted(zip(FEATURES, model.feature_importance(importance_type="gain")), key=lambda x: -x[1])
        metrics = {
            "head": label,
            "features": FEATURES,
            "n_features": len(FEATURES),
            "train_range": [args.start, args.end],
            "n_rows": len(df),
            "n_races": int(df["race_id"].nunique()),
            "num_rounds": NUM_ROUNDS,
            "seed": PARAMS["seed"],
            "feature_importance": [{"feature": f, "gain": int(g)} for f, g in importance],
        }
        with open(MODELS_DIR / f"{out_name}_metrics.json", "w") as fh:
            json.dump(metrics, fh, indent=2, ensure_ascii=False)
        logger.info("保存完了[%s]: %s.txt  重要度top5=%s",
                    label, out_name, [f"{f}:{g}" for f, g in importance[:5]])


if __name__ == "__main__":
    main()
