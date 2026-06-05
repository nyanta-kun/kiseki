"""地方競馬 v11 LightGBM 学習スクリプト

v10 (15特徴量) に加え、バックテスト検証済みの新規シグナル4つを追加（計19特徴量）:
  新規:
    improving_form    : 直近2走で着順改善 (0/1)
    track_win_rate    : 同コース累積勝率（3走未満は -1 = unknown）
    class_drop_ratio  : (前走賞金 - 今走賞金) / 今走賞金（正 = 降級、負 = 昇級）
    prev_pace_ratio   : 前走通過1位 / 頭数（小 = 先行、大 = 後方）

特徴量取得方式:
  - サブ指数 (v9): calculated_indices テーブルから
  - 新規4特徴量: race_results (2022以降) から Python で前走データを集計し JOIN

訓練期間: 2024-01-01 〜 2025-09-30
検証期間: 2025-10-01 〜 2025-12-31
テスト期間: 2026-01-01 〜

使い方:
  cd backend
  .venv/bin/python scripts/train_chihou_v11_lightgbm.py
  .venv/bin/python scripts/train_chihou_v11_lightgbm.py --objective rank
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
logger = logging.getLogger("chihou_v11_train")

CHIHOU_V9_VERSION = 9
MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
# 特徴量定義
# ─────────────────────────────────────────────
SUBINDEX_FEATURES = [
    "speed_index", "last3f_index", "jockey_index", "rotation_index", "last_margin_index",
]
RACE_FEATURES = [
    "distance", "head_count",
    "is_turf", "is_dirt",
    "is_good", "is_heavy", "is_bad",
]
HORSE_FEATURES = [
    "frame_number", "horse_age", "weight_carried", "horse_weight", "weight_change",
]
NEW_FEATURES = [
    "improving_form",    # 直近2走で着順改善 (0/1, -1=不明)
    "track_win_rate",    # 同コース累積勝率 (-1=3走未満/不明)
    "class_drop_ratio",  # (prev_prize - curr_prize) / curr_prize (-9=不明)
    "prev_pace_ratio",   # 前走通過1位/頭数 (-1=不明)
]
ALL_FEATURES = SUBINDEX_FEATURES + RACE_FEATURES + HORSE_FEATURES + NEW_FEATURES

# ─────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────
BASE_QUERY = """
SELECT
    ci.race_id,
    r.date,
    r.course_name,
    r.prize_1st     AS curr_prize,
    r.surface,
    r.condition,
    r.distance,
    r.head_count,
    re.horse_id,
    re.frame_number,
    re.horse_age,
    re.weight_carried,
    COALESCE(rr.horse_weight, 500) AS horse_weight,
    COALESCE(rr.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    rr.finish_position,
    rr.win_odds
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = %(ver)s
  AND r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
  AND rr.win_odds IS NOT NULL
ORDER BY r.date, ci.race_id, re.horse_id
"""

HIST_QUERY = """
SELECT
    rr.horse_id,
    r.id        AS race_id,
    r.date,
    r.course_name,
    r.prize_1st,
    r.head_count,
    rr.finish_position,
    rr.passing_1
FROM chihou.race_results rr
JOIN chihou.races r ON r.id = rr.race_id
WHERE r.course != '83'
  AND r.date >= '20220101'
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY rr.horse_id, r.date, r.id
"""


# ─────────────────────────────────────────────
# 特徴量エンジニアリング
# ─────────────────────────────────────────────

def featurize(df: pd.DataFrame) -> pd.DataFrame:
    """サーフェス・馬場状態を one-hot へ変換する。"""
    df = df.copy()
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.contains("芝").astype(int)
    df["is_dirt"] = s.str.contains("ダ").astype(int)
    c = df["condition"].fillna("").astype(str)
    df["is_good"]  = (c == "良").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"]   = (c == "不").astype(int)
    for col in ALL_FEATURES:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def add_historical_features(
    df: pd.DataFrame,
    hist: pd.DataFrame,
) -> pd.DataFrame:
    """履歴データから新規4特徴量を計算して df に付与する。

    すべて「現レース開始前時点」のデータのみ使用（データリークなし）。

    Args:
        df   : ベーストレーニングデータ (race_id / horse_id / curr_prize / course_name 必須)
        hist : chihou.race_results 全件 (horse_id / race_id / date / course_name /
               prize_1st / head_count / finish_position / passing_1)
    """
    logger.info("新規特徴量計算中 (履歴データ %d行)...", len(hist))

    # ── hist を horse_id → date → race_id でソート ──
    hist = hist.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)

    # ── 前走・2走前 の finish_position / prize_1st / passing_1 / head_count ──
    g = hist.groupby("horse_id")
    hist["prev_finish"]     = g["finish_position"].shift(1)
    hist["prev2_finish"]    = g["finish_position"].shift(2)
    hist["prev_prize"]      = g["prize_1st"].shift(1)
    hist["prev_passing_1"]  = g["passing_1"].shift(1)
    hist["prev_head_count"] = g["head_count"].shift(1)

    # ── 案4: コース別累積成績（現レース除く） ──
    hist_cs = hist.sort_values(["horse_id", "course_name", "date", "race_id"]).copy()
    hist_cs["is_win"] = (hist_cs["finish_position"] == 1).astype(int)
    hist_cs["_track_runs_cumul"] = hist_cs.groupby(["horse_id", "course_name"]).cumcount()
    hist_cs["_track_wins_cumul"] = (
        hist_cs.groupby(["horse_id", "course_name"])["is_win"].cumsum()
        - hist_cs["is_win"]   # 現走分を引いて「現走前累計」にする
    )
    # race_id をキーにしてマージできるよう整理
    track_cols = hist_cs.set_index(["horse_id", "race_id"])[["_track_runs_cumul", "_track_wins_cumul"]]

    # ── signals DataFrame を作成（horse_id + race_id でインデックス） ──
    sig_cols = [
        "horse_id", "race_id",
        "prev_finish", "prev2_finish",
        "prev_prize", "prev_passing_1", "prev_head_count",
    ]
    signals = hist[sig_cols].drop_duplicates(subset=["horse_id", "race_id"])

    # ── ベースと結合 ──
    df = df.merge(signals, on=["horse_id", "race_id"], how="left")
    df = df.join(track_cols, on=["horse_id", "race_id"], how="left")

    # ── 特徴量計算 ──

    # improving_form: 0=横ばい/悪化/不明、1=改善
    df["improving_form"] = np.where(
        df["prev_finish"].notna() & df["prev2_finish"].notna(),
        (df["prev_finish"] < df["prev2_finish"]).astype(float),
        -1.0,  # 不明
    )

    # track_win_rate: 3走以上あれば実勝率、なければ -1
    runs  = df["_track_runs_cumul"].fillna(0)
    wins  = df["_track_wins_cumul"].fillna(0)
    df["track_win_rate"] = np.where(
        runs >= 3,
        (wins / runs.clip(lower=1)).clip(0.0, 1.0),
        -1.0,
    )

    # class_drop_ratio: (prev_prize - curr_prize) / curr_prize
    curr_prize = df["curr_prize"].astype(float).clip(lower=1)
    df["class_drop_ratio"] = np.where(
        df["prev_prize"].notna(),
        (df["prev_prize"].astype(float) - curr_prize) / curr_prize,
        -9.0,   # 不明フラグ
    ).clip(-2.0, 5.0)   # 外れ値クリップ（-9 は -2 にまとめない → そのまま）
    # -9 をそのまま残すのは混乱を招くので 0 (中立) に設定する
    df["class_drop_ratio"] = df["class_drop_ratio"].where(
        df["prev_prize"].notna(), 0.0
    )

    # prev_pace_ratio: passing_1 / head_count（0〜1、小 = 先行）
    phc = df["prev_head_count"].astype(float).clip(lower=1)
    df["prev_pace_ratio"] = np.where(
        df["prev_passing_1"].notna() & df["prev_head_count"].notna(),
        (df["prev_passing_1"].astype(float) / phc).clip(0.0, 1.0),
        -1.0,   # 不明
    )

    logger.info(
        "  improving_form(不明=%.1f%%): 0=%.1f%% 1=%.1f%%",
        (df["improving_form"] == -1).mean() * 100,
        (df["improving_form"] == 0).mean() * 100,
        (df["improving_form"] == 1).mean() * 100,
    )
    logger.info(
        "  track_win_rate(不明=%.1f%%):  平均=%.3f",
        (df["track_win_rate"] < 0).mean() * 100,
        df.loc[df["track_win_rate"] >= 0, "track_win_rate"].mean(),
    )
    logger.info(
        "  class_drop_ratio: 平均=%.3f (降級=%.1f%%)",
        df["class_drop_ratio"].mean(),
        (df["class_drop_ratio"] > 0.2).mean() * 100,
    )
    logger.info(
        "  prev_pace_ratio(不明=%.1f%%):  平均=%.3f",
        (df["prev_pace_ratio"] < 0).mean() * 100,
        df.loc[df["prev_pace_ratio"] >= 0, "prev_pace_ratio"].mean(),
    )

    return df


# ─────────────────────────────────────────────
# 評価
# ─────────────────────────────────────────────

def race_softmax_winprob(race_ids: pd.Series, scores: np.ndarray) -> np.ndarray:
    """レース内 min-max(15-85) → softmax(T=10) で勝率へ変換（inference_chihou_v10 と同方式）。

    旧コードは win_probability にラベル y を誤代入しており EV メトリクスが
    リークしていた（ev_filter_roi=0 の無意味値）。実スコアから確率を再構成する。
    """
    out = np.zeros(len(scores), dtype=float)
    tmp = pd.DataFrame({"race_id": np.asarray(race_ids), "score": scores})
    for _, idx in tmp.groupby("race_id").indices.items():
        s = tmp["score"].values[idx]
        if len(s) <= 1:
            out[idx] = 1.0
            continue
        lo, hi = s.min(), s.max()
        scaled = np.full(len(s), 50.0) if hi - lo < 1e-9 else 15.0 + (s - lo) / (hi - lo) * 70.0
        st = scaled / 10.0
        ex = np.exp(st - st.max())
        out[idx] = ex / ex.sum()
    return out


def evaluate(df_eval: pd.DataFrame, scores: np.ndarray, label: str) -> dict:
    """予測スコアによるレースごと1位馬の評価指標を返す。"""
    df = df_eval.copy()
    df["score"] = scores
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"]        = pd.to_numeric(df["win_odds"],        errors="coerce")

    top1 = df.loc[df.groupby("race_id")["score"].idxmax()]
    win_pct   = (top1["finish_position"] == 1).mean() * 100
    place_pct = (top1["finish_position"] <= 3).mean() * 100
    win_roi   = (top1.loc[top1["finish_position"] == 1, "win_odds"]).sum() / len(top1)

    # 穴馬フォーカス: スコア1位 ∧ 単勝≥10 での勝率・ROI
    top1_hi = top1[top1["win_odds"] >= 10]
    hi_win_pct = (top1_hi["finish_position"] == 1).mean() * 100 if len(top1_hi) else 0.0
    hi_win_roi = (
        (top1_hi.loc[top1_hi["finish_position"] == 1, "win_odds"]).sum() / len(top1_hi)
        if len(top1_hi) else 0.0
    )

    # 穴馬抽出精度: EV 1.0-1.5 ∧ 単勝≥10 でのモデル予測上位馬の勝率・ROI
    ev_cands = df[(df["win_odds"] >= 10)].copy()
    if len(ev_cands) > 0:
        ev_cands["ev"] = ev_cands["win_probability"] * ev_cands["win_odds"] if "win_probability" in ev_cands.columns else 0
    ev_top1 = ev_cands.loc[ev_cands.groupby("race_id")["score"].idxmax()] if len(ev_cands) else pd.DataFrame()
    ev_top1_filtered = ev_top1[
        (ev_top1.get("ev", pd.Series(dtype=float)) >= 1.0) &
        (ev_top1.get("ev", pd.Series(dtype=float)) < 1.5)
    ] if len(ev_top1) else pd.DataFrame()
    ev_roi = (
        (ev_top1_filtered.loc[ev_top1_filtered["finish_position"] == 1, "win_odds"]).sum()
        / len(ev_top1_filtered)
        if len(ev_top1_filtered) > 0 else 0.0
    )

    metrics = {
        "label":            label,
        "n_races":          len(top1),
        "n_horses":         len(df),
        "top1_win_pct":     round(win_pct, 2),
        "top1_place_pct":   round(place_pct, 2),
        "top1_win_roi":     round(win_roi, 3),
        "top1_hi_n":        len(top1_hi),
        "top1_hi_win_pct":  round(hi_win_pct, 2),
        "top1_hi_win_roi":  round(hi_win_roi, 3),
        "ev_filter_n":      len(ev_top1_filtered),
        "ev_filter_roi":    round(ev_roi, 3),
    }
    logger.info("[%s] win=%.1f%% place=%.1f%% ROI=%.3f | hi_odds win=%.1f%% ROI=%.3f",
                label, win_pct, place_pct, win_roi, hi_win_pct, hi_win_roi)
    return metrics


# ─────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────

def fetch_base(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    logger.info("base取得: %d行, %d レース (%s〜%s)", len(df), df["race_id"].nunique(), start, end)
    return df


def fetch_hist(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(HIST_QUERY)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    logger.info("履歴取得: %d行, %d 馬", len(df), df["horse_id"].nunique())
    return df


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--objective", choices=["binary", "rank"], default="binary")
    p.add_argument("--train-start", default="20240101")
    p.add_argument("--train-end",   default="20250930")
    p.add_argument("--valid-start", default="20251001")
    p.add_argument("--valid-end",   default="20251231")
    p.add_argument("--test-start",  default="20260101")
    p.add_argument("--test-end",    default="20260503")
    p.add_argument("--num-leaves",        type=int,   default=31)
    p.add_argument("--max-depth",         type=int,   default=5)
    p.add_argument("--min-data-in-leaf",  type=int,   default=50)
    p.add_argument("--lambda-l1",         type=float, default=0.1)
    p.add_argument("--lambda-l2",         type=float, default=1.0)
    p.add_argument("--feature-fraction",  type=float, default=0.8)
    p.add_argument("--bagging-fraction",  type=float, default=0.8)
    p.add_argument("--learning-rate",     type=float, default=0.05)
    p.add_argument("--num-iterations",    type=int,   default=600)
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    logger.info("データ取得中...")
    df_train_raw = fetch_base(conn, args.train_start, args.train_end)
    df_valid_raw = fetch_base(conn, args.valid_start, args.valid_end)
    df_test_raw  = fetch_base(conn, args.test_start,  args.test_end)
    df_hist      = fetch_hist(conn)
    conn.close()

    # ── 全期間をまとめて特徴量エンジニアリング（leakage を避けるため全件をhistに渡す） ──
    df_all_raw = pd.concat([df_train_raw, df_valid_raw, df_test_raw], ignore_index=True)
    df_all = featurize(df_all_raw)
    df_all = add_historical_features(df_all, df_hist)

    split_train = df_all["date"] <= df_train_raw["date"].max()
    split_valid = (df_all["date"] > df_train_raw["date"].max()) & (df_all["date"] <= df_valid_raw["date"].max())
    split_test  = df_all["date"] > df_valid_raw["date"].max()
    df_train = df_all[split_train].copy()
    df_valid = df_all[split_valid].copy()
    df_test  = df_all[split_test].copy()

    logger.info("期間分割: train=%d valid=%d test=%d", len(df_train), len(df_valid), len(df_test))

    # ── 目的変数 ──
    for d in [df_train, df_valid, df_test]:
        d["y"] = (pd.to_numeric(d["finish_position"], errors="coerce") <= 3).astype(int)

    # ── NaN 補完（LightGBM は NaN を自動処理するが、明示的に埋める） ──
    for col in NEW_FEATURES:
        for d in [df_train, df_valid, df_test]:
            d[col] = pd.to_numeric(d[col], errors="coerce").fillna(-1.0)

    X_train = df_train[ALL_FEATURES].values.astype(float)
    X_valid = df_valid[ALL_FEATURES].values.astype(float)
    X_test  = df_test[ALL_FEATURES].values.astype(float)

    if args.objective == "rank":
        df_train = df_train.sort_values("race_id").reset_index(drop=True)
        df_valid = df_valid.sort_values("race_id").reset_index(drop=True)
        X_train = df_train[ALL_FEATURES].values.astype(float)
        X_valid = df_valid[ALL_FEATURES].values.astype(float)
        rel_train = (5 - pd.to_numeric(df_train["finish_position"], errors="coerce").fillna(20)).clip(lower=0).astype(int).values
        rel_valid = (5 - pd.to_numeric(df_valid["finish_position"], errors="coerce").fillna(20)).clip(lower=0).astype(int).values
        g_train = df_train.groupby("race_id", sort=False).size().values
        g_valid = df_valid.groupby("race_id", sort=False).size().values
        params = {
            "objective":        "lambdarank",
            "metric":           "ndcg",
            "ndcg_eval_at":     [1, 3],
            "num_leaves":       args.num_leaves,
            "max_depth":        args.max_depth,
            "min_data_in_leaf": args.min_data_in_leaf,
            "lambda_l1":        args.lambda_l1,
            "lambda_l2":        args.lambda_l2,
            "learning_rate":    args.learning_rate,
            "feature_fraction": args.feature_fraction,
            "bagging_fraction": args.bagging_fraction,
            "bagging_freq":     5,
            "verbose":          -1,
        }
        train_set = lgb.Dataset(X_train, rel_train, group=g_train, feature_name=ALL_FEATURES)
        valid_set = lgb.Dataset(X_valid, rel_valid, group=g_valid, feature_name=ALL_FEATURES,
                                reference=train_set)
    else:
        y_train = df_train["y"].values
        y_valid = df_valid["y"].values
        params = {
            "objective":        "binary",
            "metric":           "binary_logloss",
            "num_leaves":       args.num_leaves,
            "max_depth":        args.max_depth,
            "min_data_in_leaf": args.min_data_in_leaf,
            "lambda_l1":        args.lambda_l1,
            "lambda_l2":        args.lambda_l2,
            "learning_rate":    args.learning_rate,
            "feature_fraction": args.feature_fraction,
            "bagging_fraction": args.bagging_fraction,
            "bagging_freq":     5,
            "verbose":          -1,
        }
        train_set = lgb.Dataset(X_train, y_train, feature_name=ALL_FEATURES)
        valid_set = lgb.Dataset(X_valid, y_valid, feature_name=ALL_FEATURES, reference=train_set)

    logger.info(
        "学習開始 objective=%s leaves=%d depth=%d iter=%d",
        args.objective, args.num_leaves, args.max_depth, args.num_iterations,
    )
    model = lgb.train(
        params,
        train_set,
        num_boost_round=args.num_iterations,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(50)],
    )

    s_train = model.predict(X_train, num_iteration=model.best_iteration)
    s_valid = model.predict(X_valid, num_iteration=model.best_iteration)
    s_test  = model.predict(X_test,  num_iteration=model.best_iteration)

    metrics = {}
    # win_probability はモデルスコアのレース内 softmax から再構成（旧: ラベル y を誤代入＝リーク）
    df_train["win_probability"] = race_softmax_winprob(df_train["race_id"], s_train)
    df_valid["win_probability"] = race_softmax_winprob(df_valid["race_id"], s_valid)
    df_test["win_probability"]  = race_softmax_winprob(df_test["race_id"],  s_test)

    metrics["train"] = evaluate(df_train, s_train, "train")
    metrics["valid"] = evaluate(df_valid, s_valid, "valid")
    metrics["test"]  = evaluate(df_test,  s_test,  "test")

    # ── 特徴量重要度 ──
    importance = sorted(
        zip(ALL_FEATURES, model.feature_importance(importance_type="gain")),
        key=lambda x: -x[1],
    )
    metrics["feature_importance"] = [{"feature": f, "gain": int(g)} for f, g in importance]
    logger.info("特徴量重要度 top12:")
    for f, g in importance[:12]:
        logger.info("  %s: %d", f, g)

    # ── v10 との比較のため穴馬ROI を直接計算 ──
    logger.info("--- 穴馬フィルタ ROI 比較 ---")
    for period_label, df_p, scores in [
        ("train", df_train, s_train),
        ("valid", df_valid, s_valid),
        ("test",  df_test,  s_test),
    ]:
        df_p = df_p.copy()
        df_p["score"] = scores
        df_p["win_odds"] = pd.to_numeric(df_p["win_odds"], errors="coerce")
        df_p["finish_position"] = pd.to_numeric(df_p["finish_position"], errors="coerce")
        # 指数スコアでレース内ランク付け
        df_p["ci_rank"] = df_p.groupby("race_id")["score"].rank(ascending=False, method="min").astype(int)
        df_p["ev"] = df_p["score"] * df_p["win_odds"]   # score ≒ P(top3)、EVの代理

        # 単勝≥10 ∧ スコアでレース内 rank4+ の馬（穴馬候補）
        hi = df_p[df_p["win_odds"] >= 10]
        if not hi.empty:
            wins  = (hi["finish_position"] == 1)
            roi_v = hi.loc[wins, "win_odds"].sum() / len(hi)
            logger.info("  [%s] 単勝≥10全体: ROI=%.3f n=%d", period_label, roi_v, len(hi))
            # rank4+
            hi4 = hi[hi["ci_rank"] >= 4]
            if not hi4.empty:
                roi4 = hi4.loc[hi4["finish_position"] == 1, "win_odds"].sum() / len(hi4)
                logger.info("  [%s] 単勝≥10 ∧ rank4+: ROI=%.3f n=%d", period_label, roi4, len(hi4))

    suffix = args.objective
    model_path   = MODELS_DIR / f"chihou_v11_lightgbm_{suffix}.txt"
    metrics_path = MODELS_DIR / f"chihou_v11_metrics_{suffix}.json"
    model.save_model(str(model_path))
    with open(metrics_path, "w") as fh:
        json.dump(metrics, fh, indent=2, ensure_ascii=False, default=str)
    logger.info("完了: model=%s", model_path)
    logger.info("      metrics=%s", metrics_path)


if __name__ == "__main__":
    main()
