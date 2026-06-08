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
# Phase5(2026-06-08): 馬場コンディション関連特徴(リーン4本)。
# 良/稍/重/不 の単体グラデーション(track_wetness)は gain≈0(コンディションの時計効果は
# speed_index の条件別 par_time で既に吸収済み)のため不採用。価値は「馬個体の道悪適性 ×
# 脚質交互」に集約。全て発走前算出・リークなし point-in-time。
# A/B(5seed×2cutoff 決定論OOS, scripts/ab_chihou_track_condition.py AB_LEAN=1):
# 全体 top1勝率/複勝率 ともに微増(+0.04〜0.13pt)・悪化なし。serve 側は
# chihou_calculator._wet_apt_batch / _build_lgb_features と同順・同意味論にすること。
#   pace_x_wet           : 先行度(prev_pace_ratio, 不明→0.5) × track_wetness
#   horse_wet_apt        : 過去道悪走スコア − 過去全走スコア(≥2道悪走, else 0, field正規化着順)
#   horse_wet_apt_active : horse_wet_apt × (現在 重/不 か)
#   horse_wet_runs       : 過去道悪経験数 min(n,20)/20
TRACK_FEATURES = ["pace_x_wet", "horse_wet_apt", "horse_wet_apt_active", "horse_wet_runs"]
FEATURES = FEATURES + HIST_FEATURES + EXT_FEATURES + TRACK_FEATURES

WET_CONDS = ("重", "不")
WETNESS_MAP = {"良": 0.0, "稍": 1.0, "重": 2.0, "不": 3.0}

HIST_COND_QUERY = """
SELECT rr.horse_id, r.id AS race_id, r.date, r.condition,
       rr.finish_position, r.head_count
FROM chihou.race_results rr
JOIN chihou.races r ON r.id = rr.race_id
WHERE r.course != '83'
  AND r.date >= '20220101'
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY rr.horse_id, r.date, r.id
"""

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


def fetch_hist_cond(conn) -> pd.DataFrame:
    """馬場適性算出用の履歴（condition 込み）を取得する。"""
    cur = conn.cursor()
    cur.execute(HIST_COND_QUERY)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def compute_wet_apt_table(hist: pd.DataFrame) -> pd.DataFrame:
    """履歴から馬の道悪適性を point-in-time(現走前累積)で算出する。

    serve 側 chihou_calculator._wet_apt_batch と同一意味論にすること。
    返り値: (horse_id, race_id) を index とする [horse_wet_apt, horse_wet_runs]。
    """
    h = hist.copy()
    h["fp"] = pd.to_numeric(h["finish_position"], errors="coerce")
    h["hc"] = pd.to_numeric(h["head_count"], errors="coerce")
    h["score"] = (1.0 - (h["fp"] - 1.0) / (h["hc"] - 1.0)).clip(0.0, 1.0)
    h.loc[h["hc"] < 2, "score"] = np.nan  # 頭数<2 はスコア無効
    h["wet"] = h["condition"].isin(WET_CONDS).astype(float)
    h = h.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    g = h.groupby("horse_id")
    h["valid"] = h["score"].notna().astype(float)
    h["s"] = h["score"].fillna(0.0)
    h["all_cnt"] = g["valid"].cumsum() - h["valid"]          # 現走前の有効走数
    h["all_sum"] = g["s"].cumsum() - h["s"]
    h["wscore"] = h["s"] * h["wet"]
    h["wet_valid"] = h["valid"] * h["wet"]
    h["wet_cnt"] = g["wet_valid"].cumsum() - h["wet_valid"]  # 現走前の道悪有効走数
    h["wet_sum"] = g["wscore"].cumsum() - h["wscore"]
    base = h["all_sum"] / h["all_cnt"].clip(lower=1)
    wetperf = h["wet_sum"] / h["wet_cnt"].clip(lower=1)
    h["horse_wet_apt"] = np.where(h["wet_cnt"] >= 2, (wetperf - base).clip(-1.0, 1.0), 0.0)
    h["horse_wet_runs"] = h["wet_cnt"].clip(upper=20) / 20.0
    return h.set_index(["horse_id", "race_id"])[["horse_wet_apt", "horse_wet_runs"]]


def add_track_features(df: pd.DataFrame, apt_tbl: pd.DataFrame) -> pd.DataFrame:
    """馬場コンディション関連特徴(リーン4本)を付与する（serve と同順・同意味論）。

    track_wetness はモデル特徴には含めず交互作用の素材としてのみ内部利用する。
    """
    df = df.copy()
    cond = df["condition"].fillna("").astype(str)
    wetness = cond.map(WETNESS_MAP).fillna(1.0)  # 不明→稍(中立)
    pace = pd.to_numeric(df.get("prev_pace_ratio"), errors="coerce")
    pace = pace.where(pace >= 0, 0.5)            # 不明(-1)→中立0.5
    df["pace_x_wet"] = pace * wetness
    df = df.join(apt_tbl, on=["horse_id", "race_id"], how="left")
    df["horse_wet_apt"] = df["horse_wet_apt"].fillna(0.0)
    df["horse_wet_runs"] = df["horse_wet_runs"].fillna(0.0)
    df["horse_wet_apt_active"] = df["horse_wet_apt"] * (wetness >= 2).astype(float)
    return df


def prep(conn, df_raw: pd.DataFrame, df_hist: pd.DataFrame) -> pd.DataFrame:
    """featurize + 履歴系4特徴 + 外部指数特徴 + 馬場特徴 + 欠損補完（train/serve 整合）。"""
    df = featurize(df_raw)
    df = add_historical_features(df, df_hist)
    for col in HIST_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1.0)
    df = add_external_features(df)
    df = add_track_features(df, compute_wet_apt_table(fetch_hist_cond(conn)))
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
