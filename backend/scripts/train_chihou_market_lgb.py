"""Phase 2: 市場対比 LambdaRank モデル A/B テスト

■ 設計思想
  現行モデル(is_top3 binary): 「着順top3かどうか」を予測
  本モデル(market-aware LambdaRank):
    「市場(人気/オッズ)が正しく評価したか、外したかを踏まえた上で、
     馬の最終着順を予測する」

■ Relevance ラベル設計（market_relevance）
  人気ティア × 着順で relevance を設定し、以下の指数行動を誘導する:

    穴馬(7番人気以上)1着:   6  ← 最高評価 (市場外れを捕捉)
    穴馬複勝(2-3着):         4
    中間人気(4-6番人気)1着:  4
    中間人気複勝:            3
    本命(1-3番人気)1着:      3  ← 市場正確を確認
    本命複勝:                2
    中間人気/穴馬着外:       1  ← 中立
    本命着外:                0  ← 最低 (市場誤りの格下げシグナル)

  この設計により:
    「市場が評価し、評価通りに来た」 → relevance 2-3 (上位)
    「市場が評価したが、来なかった」 → relevance 0   (最低 = 格下げ)
    「市場が評価せず、来た」         → relevance 4-6 (最高 = 穴馬発見)

■ 市場乖離特徴量（追加5特徴）
  odds_rank_n     : レース内オッズ順位/頭数 (0=1番人気=best, 1=最高人気)
  speed_mkt_gap   : odds_rank_n - speed_rank_n  (正=速度指数>市場評価)
  kc_mkt_gap      : odds_rank_n - kc_rank_n      (正=外部指数>市場評価)
  is_heavy_fav    : 断然人気フラグ (win_popularity <= 2)
  is_dark_horse   : 穴馬フラグ (win_popularity >= 7)

  これらにより、「指数は良いがオッズが高い(市場が見落とした)馬」を
  モデルが独立に学習できる。

■ OOS 評価（学習・検証・テスト汚染防止）
  cutoff1: train ≤ 20250630  / test 20250701〜20260706
  cutoff2: train ≤ 20241231  / test 20250101〜20260706
  5 seeds × 2 cutoffs = 10 評価点 (deterministic=True で seed 再現)

■ 3カテゴリ評価指標
  cat_dark_found  : 指数1位 ∩ 市場非1位 ∩ 1・2着  (穴馬独自発見率)
  cat_mkt_confirm : 指数1位 ∩ 市場1位 ∩ 1・2着   (市場一致確認率)
  cat_mkt_miss    : 指数1位 ∩ 市場1位 ∩ 3着以下  (本命外れ見逃し率)
  fav_downgrade   : 本命(3人気以内)着外馬を指数低位置(下位半分)に置けた率

使い方:
  cd backend
  .venv/bin/python scripts/train_chihou_market_lgb.py
  .venv/bin/python scripts/train_chihou_market_lgb.py --save-model
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

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root.parent / ".env")

# 既存 prod スクリプトから履歴系特徴計算を再利用
from scripts.train_chihou_prod_lgb import (  # noqa: E402
    CHIHOU_V9_VERSION,
    FEATURES as PROD_FEATURES,
    HIST_FEATURES,
    add_corner_trainer_features,
    add_external_features,
    add_historical_features,  # re-exported from train_chihou_v11_lightgbm
    add_track_features,
    build_ct_tables,
    compute_wet_apt_table,
    featurize,
    fetch_hist,               # re-exported from train_chihou_v11_lightgbm
    fetch_hist_cond,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_market_lgb")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
# OOS 評価期間定義（データ汚染防止）
# ─────────────────────────────────────────────
# cutoff: モデルの学習終了日。この日以降のデータはテスト期間（学習に一切使わない）
CUTOFFS = [
    {"cutoff": "20250630", "test_start": "20250701", "test_end": "20260706",
     "label": "cut1(〜25/06)"},
    {"cutoff": "20241231", "test_start": "20250101", "test_end": "20260706",
     "label": "cut2(〜24/12)"},
]
TRAIN_DATA_START = "20230101"  # 全期間の学習開始日
SEEDS = [0, 1, 2, 3, 4]       # 5 seeds for robustness
NUM_ROUNDS = 400

# ─────────────────────────────────────────────
# 特徴量定義
# ─────────────────────────────────────────────
# 市場乖離特徴（新設 5本）— 既存 PROD_FEATURES に追記
MARKET_FEATURES = [
    "odds_rank_n",     # レース内オッズ順位/頭数 (0=1番人気=best, 1=最高人気=worst)
    "speed_mkt_gap",   # odds_rank_n - speed_rank_n (正=速度指数>市場評価=市場が過小評価)
    "kc_mkt_gap",      # odds_rank_n - kc_rank_n    (正=外部指数>市場評価)
    "is_heavy_fav",    # 断然人気 (popularity <= 2)
    "is_dark_horse",   # 穴馬 (popularity >= 7)
]
ALL_FEATURES = PROD_FEATURES + MARKET_FEATURES

# ─────────────────────────────────────────────
# SQL: win_popularity/win_odds を追加取得
# ─────────────────────────────────────────────
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
    rr.finish_position,
    rr.win_odds,
    rr.win_popularity,
    CASE WHEN nk.idx_ave ~ '^-?[0-9]+\\*?$'
         THEN regexp_replace(nk.idx_ave, '\\*', '')::float ELSE NULL END AS nk_idx,
    kc.sp_score AS kc_sp
FROM (
    -- v9 優先・なければ最新 version（train_chihou_prod_lgb.BASE_QUERY と同一方針）
    SELECT DISTINCT ON (race_id, horse_id)
        race_id, horse_id, speed_index, last3f_index, jockey_index,
        rotation_index, last_margin_index
    FROM chihou.calculated_indices
    WHERE version >= %(ver)s
    ORDER BY race_id, horse_id, (version = %(ver)s) DESC, version DESC
) ci
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
WHERE r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY r.date, ci.race_id
"""


def fetch(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    for col in ["finish_position", "win_odds", "win_popularity", "head_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ─────────────────────────────────────────────
# 市場 Relevance ラベル
# ─────────────────────────────────────────────

def compute_market_relevance(df: pd.DataFrame) -> np.ndarray:
    """市場対比 relevance ラベルを計算する。

    Returns:
        非負整数 ndarray (LightGBM LambdaRank 用)
    """
    fp = pd.to_numeric(df["finish_position"], errors="coerce").fillna(99).astype(int)
    pop = pd.to_numeric(df["win_popularity"], errors="coerce").fillna(5).astype(int)

    # 人気ティア
    is_fav    = pop <= 3   # 1-3番人気: 本命
    is_mid    = (pop >= 4) & (pop <= 6)   # 4-6番人気: 中間
    is_dark   = pop >= 7   # 7番人気以上: 穴馬

    # 着順ティア
    win   = fp == 1
    place = (fp >= 2) & (fp <= 3)
    out   = fp >= 4

    rel = np.ones(len(df), dtype=np.int32)  # デフォルト 1 (中立)

    # 本命
    rel[is_fav & win]   = 3   # 本命的中 → 市場正確を確認
    rel[is_fav & place] = 2   # 本命複勝
    rel[is_fav & out]   = 0   # 本命着外 → 最低（格下げシグナル）

    # 中間人気
    rel[is_mid & win]   = 4
    rel[is_mid & place] = 3
    rel[is_mid & out]   = 1   # 中立

    # 穴馬
    rel[is_dark & win]   = 6   # 穴馬1着 → 最高評価
    rel[is_dark & place] = 4   # 穴馬複勝
    rel[is_dark & out]   = 1   # 穴馬着外 → 中立（元々来なくて当然）

    return rel


# ─────────────────────────────────────────────
# 市場乖離特徴量
# ─────────────────────────────────────────────

def add_market_features(df: pd.DataFrame) -> pd.DataFrame:
    """市場乖離特徴を付与する。

    odds_rank_n     : レース内オッズ昇順ランク/頭数 (0=1番人気=best)
    speed_rank_n    : speed_index 降順ランク/頭数  (0=最高速度=best)
    speed_mkt_gap   : odds_rank_n - speed_rank_n
                      正 → 速度指数が市場より高評価(市場が過小評価)
                      負 → 市場が速度指数より高評価(本命だが速度指数低め)
    kc_mkt_gap      : odds_rank_n - kc_rank_n (kc_rank_n は既存特徴, 0=best)
                      正 → 外部指数が市場より高評価
    is_heavy_fav    : win_popularity <= 2 の断然人気フラグ
    is_dark_horse   : win_popularity >= 7 の穴馬フラグ

    NOTE: win_popularity を直接特徴に入れると「人気のある馬を上位に置く」学習に
    なる可能性があるため、フラグ形式 + 相対特徴(gap)に限定する。
    """
    df = df.copy()
    pop = pd.to_numeric(df["win_popularity"], errors="coerce")
    hc = pd.to_numeric(df["head_count"], errors="coerce").clip(lower=1)
    g = df.groupby("race_id")

    # オッズ順位 (昇順: 低オッズ=1番人気=rank1 → odds_rank_n=0)
    df["odds_rank_n"] = (
        (g["win_odds"].rank(ascending=True, method="min", na_option="bottom") - 1) / hc
    ).fillna(0.5)

    # speed_index 降順ランク (高速度=rank1 → speed_rank_n=0)
    df["speed_rank_n"] = (
        (g["speed_index"].rank(ascending=False, method="min") - 1) / hc
    ).fillna(0.5)

    # 市場乖離ギャップ
    # 正 = 指数が市場より高く評価（市場の過小評価 = 穴馬候補）
    # 負 = 市場が指数より高く評価（指数の過小評価 = 本命外れリスク）
    df["speed_mkt_gap"] = (df["odds_rank_n"] - df["speed_rank_n"]).clip(-1.0, 1.0)
    # kc_rank_n は add_external_features で既に付与済み (0=best, 0.5=欠損)
    df["kc_mkt_gap"]    = (df["odds_rank_n"] - df["kc_rank_n"].fillna(0.5)).clip(-1.0, 1.0)

    # 人気フラグ（カテゴリフラグのみ。生の人気番号は直接特徴に入れない）
    df["is_heavy_fav"]  = (pop <= 2).astype(int).where(pop.notna(), 0)
    df["is_dark_horse"] = (pop >= 7).astype(int).where(pop.notna(), 0)

    return df


# ─────────────────────────────────────────────
# データ準備パイプライン
# ─────────────────────────────────────────────

def prep(conn, df_raw: pd.DataFrame, df_hist: pd.DataFrame) -> pd.DataFrame:
    """featurize → 履歴系 → 外部指数 → 馬場 → 市場乖離 の順で特徴付与する。
    train_chihou_prod_lgb.prep と同一前処理 + 市場乖離5特徴を追加。
    """
    df = featurize(df_raw)
    df = add_historical_features(df, df_hist)
    for col in HIST_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1.0)
    df = add_external_features(df)
    df = add_track_features(df, compute_wet_apt_table(fetch_hist_cond(conn)))
    df = add_corner_trainer_features(df, *build_ct_tables(conn))  # Phase6 CT9特徴
    df = add_market_features(df)   # 市場乖離特徴（新規追加）

    # market relevance ラベルを付与
    df["market_relevance"] = compute_market_relevance(df)
    return df


# ─────────────────────────────────────────────
# LambdaRank 学習
# ─────────────────────────────────────────────

def build_lambdarank_dataset(df: pd.DataFrame, features: list[str]) -> tuple:
    """LambdaRank 用に (X, relevance, group) を返す。

    NOTE:
    - data は race_id で昇順ソート済み前提 (GROUP 構造が連続していること)
    - group = [n_horses_race1, n_horses_race2, ...]
    - relevance は非負整数
    """
    df_s = df.sort_values("race_id").reset_index(drop=True)
    X    = df_s[features].fillna(0.0).values.astype(np.float64)
    y    = df_s["market_relevance"].values.astype(np.int32)
    group = df_s.groupby("race_id").size().values.astype(np.int32)
    return X, y, group, df_s


def train_lambdarank(X_tr, y_tr, group_tr, seed: int) -> lgb.Booster:
    """LambdaRank モデルを学習する。

    deterministic=True + force_col_wise=True で seed 固定の決定論的動作を保証する。
    """
    params = {
        "objective":       "lambdarank",
        "metric":          "ndcg",
        "ndcg_eval_at":    [1, 2, 3],    # NDCG@1/2/3 を監視
        "num_leaves":      31,
        "max_depth":       5,
        "min_data_in_leaf": 50,
        "lambda_l1":       0.1,
        "lambda_l2":       1.0,
        "learning_rate":   0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq":    5,
        "seed":            seed,
        "verbose":         -1,
        "deterministic":   True,   # seed 再現性必須（メモリ: ab_chihou_track_condition.md）
        "force_col_wise":  True,
    }
    ds = lgb.Dataset(X_tr, label=y_tr, group=group_tr,
                     feature_name=ALL_FEATURES, free_raw_data=False)
    return lgb.train(params, ds, num_boost_round=NUM_ROUNDS)


# ─────────────────────────────────────────────
# 評価指標
# ─────────────────────────────────────────────

def evaluate(df_test: pd.DataFrame, scores: np.ndarray, label: str) -> dict:
    """全指標を計算して dict で返す。

    df_test には race_id / finish_position / win_popularity が必要。
    scores は df_test と同順の連続スコア。高いほど上位。
    """
    d = df_test.copy()
    d["score"] = scores
    d["fp"]  = pd.to_numeric(d["finish_position"], errors="coerce")
    d["pop"] = pd.to_numeric(d["win_popularity"], errors="coerce")

    # レース内ランキング (降順: 高スコア=1位)
    d["idx_rank"] = d.groupby("race_id")["score"].rank(ascending=False, method="min")
    # 人気ランク (昇順: 低人気番号=1番人気)
    d["pop_rank"] = d.groupby("race_id")["pop"].rank(ascending=True, method="min",
                                                       na_option="bottom")

    metrics: dict[str, float] = {}
    race_rows = []
    for race_id, g in d.groupby("race_id"):
        t1 = g[g["idx_rank"] == 1]
        if t1.empty:
            continue
        fp1  = int(t1.iloc[0]["fp"])
        pop1 = t1.iloc[0]["pop"]
        is_mkt1 = t1.iloc[0]["pop_rank"] == 1  # 指数1位が市場1位(1番人気)か

        # top3 coverage
        idx_top3 = set(g.nsmallest(3, "idx_rank")["horse_id"])
        act_top3 = set(g.nsmallest(3, "fp")["horse_id"])
        covered  = len(idx_top3 & act_top3)

        # 本命（1-3人気）で着外した馬を指数下位半分に置けているか
        fav_out = g[(g["pop"] <= 3) & (g["fp"] >= 4)]
        fav_down_n = (fav_out["idx_rank"] > len(g) / 2).sum() if not fav_out.empty else 0
        fav_total_n = len(fav_out)

        race_rows.append({
            "race_id":        race_id,
            "course_name":    g.iloc[0]["course_name"],
            "fp1":            fp1,
            "m0_win":         int(fp1 == 1),
            "m1_top2":        int(fp1 <= 2),
            "m1_top3":        int(fp1 <= 3),
            "cover3":         int(covered == 3),
            "cover2":         int(covered >= 2),
            # 3カテゴリ指標
            # A: 指数1位 ∩ 市場非1位 ∩ 1・2着 (穴馬独自発見)
            "cat_dark_found":  int((not is_mkt1) and (fp1 <= 2)),
            # B: 指数1位 ∩ 市場1位 ∩ 1・2着 (市場一致確認)
            "cat_mkt_confirm": int(is_mkt1 and (fp1 <= 2)),
            # C: 指数1位 ∩ 市場1位 ∩ 3着以下 (本命外れ見逃し)
            "cat_mkt_miss":    int(is_mkt1 and (fp1 >= 3)),
            # D: 指数1位 ∩ 市場非1位 ∩ 3着以下 (独自読みが外れた)
            "cat_dark_miss":   int((not is_mkt1) and (fp1 >= 3)),
            # 本命格下げ: 本命で着外した馬を指数下位半分に置いた比率
            "fav_down_n":      fav_down_n,
            "fav_total_n":     fav_total_n,
            # 穴馬top3捕捉: 実際の穴馬入着を指数top3で捉えた率
            "dark_win_in_idx3": int(any(
                (g[g["horse_id"] == hid]["pop"] >= 7).any()
                for hid in (idx_top3 & act_top3)
            )),
            "has_dark_winner": int(len(g[(g["fp"] <= 3) & (g["pop"] >= 7)]) > 0),
        })

    rdf = pd.DataFrame(race_rows)
    if rdf.empty:
        return {"label": label}

    metrics["label"]     = label
    metrics["n_races"]   = len(rdf)
    metrics["M0_win"]    = round(rdf["m0_win"].mean() * 100, 2)
    metrics["M1_top2"]   = round(rdf["m1_top2"].mean() * 100, 2)
    metrics["M1_top3"]   = round(rdf["m1_top3"].mean() * 100, 2)
    metrics["M2_cover2"] = round(rdf["cover2"].mean() * 100, 2)
    metrics["M3_cover3"] = round(rdf["cover3"].mean() * 100, 2)
    metrics["cat_dark_found"]  = round(rdf["cat_dark_found"].mean() * 100, 2)
    metrics["cat_mkt_confirm"] = round(rdf["cat_mkt_confirm"].mean() * 100, 2)
    metrics["cat_mkt_miss"]    = round(rdf["cat_mkt_miss"].mean() * 100, 2)
    metrics["cat_dark_miss"]   = round(rdf["cat_dark_miss"].mean() * 100, 2)

    # 本命格下げ率 (本命着外馬のうち指数下位半分に置けた割合)
    fav_n = rdf["fav_total_n"].sum()
    metrics["fav_downgrade"] = round(
        rdf["fav_down_n"].sum() / fav_n * 100 if fav_n > 0 else 0, 2
    )

    # 穴馬入着時にtop3で捉えた率 (実際に穴馬が入着したレースのみ)
    dark_races = rdf[rdf["has_dark_winner"] == 1]
    metrics["dark_recall3"] = round(
        dark_races["dark_win_in_idx3"].mean() * 100 if not dark_races.empty else 0, 2
    )

    return metrics


def venue_breakdown(df_test: pd.DataFrame, scores: np.ndarray, label: str,
                    target_venues: list[str]) -> pd.DataFrame:
    """競馬場別の評価結果を返す。"""
    d = df_test.copy()
    d["score"] = scores
    rows = []
    for venue in target_venues:
        dv = d[d["course_name"] == venue]
        if dv["race_id"].nunique() < 20:
            continue
        m = evaluate(dv, dv["score"].values, f"{label}_{venue}")
        m["venue"] = venue
        rows.append(m)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─────────────────────────────────────────────
# Control モデル（現行 binary）の再現スコア
# ─────────────────────────────────────────────

def train_binary_control(X_tr: np.ndarray, y_tr_bin: np.ndarray,
                          seed: int,
                          feature_names: list[str] | None = None) -> lgb.Booster:
    """is_top3 binary モデルを指定の特徴量で学習する。"""
    if feature_names is None:
        feature_names = ALL_FEATURES
    params = {
        "objective":        "binary",
        "metric":           "binary_logloss",
        "num_leaves":       31,
        "max_depth":        5,
        "min_data_in_leaf": 50,
        "lambda_l1":        0.1,
        "lambda_l2":        1.0,
        "learning_rate":    0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq":     5,
        "seed":             seed,
        "verbose":          -1,
        "deterministic":    True,
        "force_col_wise":   True,
    }
    ds = lgb.Dataset(X_tr, label=y_tr_bin, feature_name=feature_names, free_raw_data=False)
    return lgb.train(params, ds, num_boost_round=NUM_ROUNDS)


# ─────────────────────────────────────────────
# メイン: Option A — binary 30特徴 vs binary 35特徴（+市場乖離）A/B テスト
# ─────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="地方競馬 市場乖離特徴追加 A/B テスト (binary 30 vs 35特徴)"
    )
    p.add_argument("--save-model", action="store_true",
                   help="A/B で 35特徴が優位な場合に本番モデルを保存する")
    p.add_argument("--cutoff-index", type=int, default=None, choices=[0, 1],
                   help="0 or 1 で cutoff を 1 つだけ指定（デバッグ用）")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    logger.info("履歴特徴テーブル読み込み中...")
    df_hist_global = fetch_hist(conn)
    apt_tbl = compute_wet_apt_table(fetch_hist_cond(conn))
    ct_tables = build_ct_tables(conn)  # Phase6 CT9特徴用（conn close 前に構築）

    # ─── 全期間データを一括取得 ───
    logger.info("全期間データ取得: %s 〜 %s", TRAIN_DATA_START, "20260706")
    df_all_raw = fetch(conn, TRAIN_DATA_START, "20260706")
    conn.close()
    logger.info("  → %d 馬行 / %d レース", len(df_all_raw), df_all_raw["race_id"].nunique())

    # ─── 特徴量付与（全期間一括） ───
    logger.info("特徴量付与中 (featurize + hist + ext + track + ct + market)...")
    df_all_raw = featurize(df_all_raw)
    df_all_raw = add_historical_features(df_all_raw, df_hist_global)
    for col in HIST_FEATURES:
        df_all_raw[col] = pd.to_numeric(df_all_raw[col], errors="coerce").fillna(-1.0)
    df_all_raw = add_external_features(df_all_raw)
    df_all_raw = add_track_features(df_all_raw, apt_tbl)
    df_all_raw = add_corner_trainer_features(df_all_raw, *ct_tables)
    df_all_raw = add_market_features(df_all_raw)
    logger.info("特徴量付与完了: %d 特徴量", len(ALL_FEATURES))

    PRIORITY_VENUES = ["大井", "川崎", "船橋", "浦和", "高知", "佐賀"]

    metric_keys = [
        ("M0_win",         "M0  指数1位勝率"),
        ("M1_top2",        "M1  指数1位→1・2着率  ★主指標"),
        ("M1_top3",        "M1  指数1位→複勝率"),
        ("M2_cover2",      "M2  top3_2頭一致率"),
        ("M3_cover3",      "M3  top3完全一致率"),
        ("cat_dark_found", "3C-A 穴馬独自発見率 (idx1位∩市場非1位∩1・2着)"),
        ("cat_mkt_confirm","3C-B 市場一致確認率 (idx1位∩市場1位∩1・2着)"),
        ("cat_mkt_miss",   "3C-C 本命外れ見逃し率 (idx1位∩市場1位∩3着以下)"),
        ("cat_dark_miss",  "3C-D 独自穴馬外れ率 (idx1位∩市場非1位∩3着以下)"),
        ("fav_downgrade",  "本命格下げ率 (本命着外馬を下位半分に置いた率)"),
        ("dark_recall3",   "穴馬入着時top3捕捉率"),
    ]

    all_results: list[dict] = []
    cutoff_list = CUTOFFS if args.cutoff_index is None else [CUTOFFS[args.cutoff_index]]
    sep = "=" * 76

    for cutoff_cfg in cutoff_list:
        cutoff     = cutoff_cfg["cutoff"]
        test_start = cutoff_cfg["test_start"]
        test_end   = cutoff_cfg["test_end"]
        c_label    = cutoff_cfg["label"]

        df_train = df_all_raw[df_all_raw["date"] <= cutoff].copy()
        df_test  = df_all_raw[
            (df_all_raw["date"] >= test_start) &
            (df_all_raw["date"] <= test_end)
        ].copy()

        n_tr = df_train["race_id"].nunique()
        n_te = df_test["race_id"].nunique()
        logger.info("\n[%s] 学習: %d レース (%s〜%s)  テスト: %d レース (%s〜%s)",
                    c_label, n_tr, TRAIN_DATA_START, cutoff, n_te, test_start, test_end)

        print(f"\n{sep}")
        print(f"■ [{c_label}] 学習={n_tr:,}レース  テスト={n_te:,}レース"
              f"  (純OOS: {test_start}〜{test_end})")
        print(sep)

        # 学習データを race_id 昇順でソート（group 構造の整合性）
        df_tr_s = df_train.sort_values("race_id").reset_index(drop=True)
        fp_tr   = pd.to_numeric(df_tr_s["finish_position"], errors="coerce")
        y_tr    = (fp_tr <= 3).astype(int).values

        X_tr_30 = df_tr_s[PROD_FEATURES].fillna(0.0).values.astype(np.float64)
        X_tr_35 = df_tr_s[ALL_FEATURES].fillna(0.0).values.astype(np.float64)
        X_te_30 = df_test[PROD_FEATURES].fillna(0.0).values.astype(np.float64)
        X_te_35 = df_test[ALL_FEATURES].fillna(0.0).values.astype(np.float64)

        seed_30: list[dict] = []
        seed_35: list[dict] = []

        for seed in SEEDS:
            logger.info("  seed=%d 学習中...", seed)

            # ── Control: binary 30特徴（現行相当） ──
            m30 = train_binary_control(X_tr_30, y_tr, seed, feature_names=PROD_FEATURES)
            s30 = m30.predict(X_te_30)
            seed_30.append(evaluate(df_test, s30, f"bin30_s{seed}"))

            # ── Treatment: binary 35特徴（+市場乖離5本） ──
            m35 = train_binary_control(X_tr_35, y_tr, seed, feature_names=ALL_FEATURES)
            s35 = m35.predict(X_te_35)
            seed_35.append(evaluate(df_test, s35, f"bin35_s{seed}"))

        def avg(results: list[dict]) -> dict:
            keys = [k for k in results[0] if isinstance(results[0][k], (int, float))]
            return {k: round(float(np.mean([r[k] for r in results])), 2) for k in keys}

        avg30 = avg(seed_30)
        avg35 = avg(seed_35)

        print(f"\n5seed平均 OOS 指標比較 [{c_label}]")
        print(f"  {'指標':<44} {'Bin35(+市場)':>13} {'Bin30(現行)':>12} {'差':>8}")
        print(f"  {'-'*44} {'-'*13} {'-'*12} {'-'*8}")
        for key, name in metric_keys:
            v35  = avg35.get(key, 0.0)
            v30  = avg30.get(key, 0.0)
            diff = v35 - v30
            sign = "↑" if diff > 0.3 else ("↓" if diff < -0.3 else " ")
            print(f"  {name:<44} {v35:>12.1f}% {v30:>11.1f}% {diff:>+7.1f}pt {sign}")

        # ─── 競馬場別詳細（最終 seed のみ） ───
        last_seed = SEEDS[-1]
        m30_l = train_binary_control(X_tr_30, y_tr, last_seed, feature_names=PROD_FEATURES)
        m35_l = train_binary_control(X_tr_35, y_tr, last_seed, feature_names=ALL_FEATURES)
        s30_l = m30_l.predict(X_te_30)
        s35_l = m35_l.predict(X_te_35)

        print(f"\n  競馬場別 M1 比較 (seed={last_seed}, OOS)")
        print(f"  {'場':<8} {'Bin35_M1%':>10} {'Bin30_M1%':>10} {'差':>6}"
              f" {'Bin35_3CA%':>11} {'Bin35_fav%':>11}")
        for venue in PRIORITY_VENUES:
            mask = df_test["course_name"].values == venue
            dv = df_test[mask]
            if dv["race_id"].nunique() < 20:
                continue
            m_v35 = evaluate(dv, s35_l[mask], f"35_{venue}")
            m_v30 = evaluate(dv, s30_l[mask], f"30_{venue}")
            d_m1  = m_v35.get("M1_top2", 0) - m_v30.get("M1_top2", 0)
            print(f"  {venue:<8} {m_v35.get('M1_top2',0):>9.1f}%"
                  f" {m_v30.get('M1_top2',0):>9.1f}%"
                  f" {d_m1:>+5.1f}pt"
                  f" {m_v35.get('cat_dark_found',0):>10.1f}%"
                  f" {m_v35.get('fav_downgrade',0):>10.1f}%")

        all_results.append({"cutoff": c_label, "avg35": avg35, "avg30": avg30})

    # ─── 総合評価 ───
    print(f"\n{sep}")
    print("■ 総合 A/B テスト評価（全 cutoff × 5seed 平均）")
    print(sep)
    if len(all_results) >= 2:
        print(f"  {'指標':<44} {'Bin35(+市場)':>13} {'Bin30(現行)':>12} {'差':>8}")
        print(f"  {'-'*44} {'-'*13} {'-'*12} {'-'*8}")
        for key, name in metric_keys:
            v35m = float(np.mean([r["avg35"].get(key, 0) for r in all_results]))
            v30m = float(np.mean([r["avg30"].get(key, 0) for r in all_results]))
            diff = v35m - v30m
            sign = "↑" if diff > 0.3 else ("↓" if diff < -0.3 else " ")
            print(f"  {name:<44} {v35m:>12.1f}% {v30m:>11.1f}% {diff:>+7.1f}pt {sign}")

    # ─── 採用基準チェック ───
    print(f"\n{sep}")
    print("■ 採用基準チェック（OOS test 全cutoff平均）")
    print(sep)
    if all_results:
        m1_35 = float(np.mean([r["avg35"].get("M1_top2", 0) for r in all_results]))
        m1_30 = float(np.mean([r["avg30"].get("M1_top2", 0) for r in all_results]))
        ca_35 = float(np.mean([r["avg35"].get("cat_dark_found", 0) for r in all_results]))
        ca_30 = float(np.mean([r["avg30"].get("cat_dark_found", 0) for r in all_results]))

        criteria = [
            ("M1(1・2着率) +0.5pt以上", m1_35 - m1_30 >= 0.5),
        ]
        all_pass = all(v for _, v in criteria)
        for cname, passed in criteria:
            print(f"  {'✅' if passed else '❌'} {cname}")
        print()

        if all_pass:
            print("  → 採用基準クリア。--save-model で本番モデルを保存可能。")
            if args.save_model:
                logger.info("全期間モデルを学習して保存中 (35特徴, binary)...")
                df_all_s = df_all_raw.sort_values("race_id").reset_index(drop=True)
                fp_all   = pd.to_numeric(df_all_s["finish_position"], errors="coerce")
                y_all    = (fp_all <= 3).astype(int).values
                X_all_35 = df_all_s[ALL_FEATURES].fillna(0.0).values.astype(np.float64)
                final_model = train_binary_control(X_all_35, y_all, seed=0)
                out_path = MODELS_DIR / "chihou_prod_lgb.v12_44feat.txt"
                final_model.save_model(str(out_path))
                logger.info("保存完了: %s", out_path)

                importance = sorted(
                    zip(ALL_FEATURES, final_model.feature_importance(importance_type="gain")),
                    key=lambda x: -x[1]
                )
                print("\n  特徴量重要度 top15 (gain):")
                for feat, gain in importance[:15]:
                    print(f"    {feat:<28} {gain:>10,}")

                result_json = {
                    "model": "chihou_prod_lgb.v12_44feat",
                    "objective": "binary",
                    "head": "is_top3",
                    "features": ALL_FEATURES,
                    "market_features": MARKET_FEATURES,
                    "n_features": len(ALL_FEATURES),
                    "train_range": [TRAIN_DATA_START, "20260706"],
                    "seeds": SEEDS,
                    "feature_importance": [
                        {"feature": f, "gain": int(g)} for f, g in importance
                    ],
                }
                with open(MODELS_DIR / "chihou_prod_lgb.v12_44feat_metrics.json", "w") as fh:
                    json.dump(result_json, fh, indent=2, ensure_ascii=False)
                print(f"\n  モデル保存完了: {out_path}")

                # win ヘッド (is_win) も35特徴で保存
                logger.info("win ヘッドを全期間学習して保存中 (35特徴, is_win)...")
                y_win = (fp_all == 1).astype(int).values
                win_model = train_binary_control(X_all_35, y_win, seed=0, feature_names=ALL_FEATURES)
                win_out = MODELS_DIR / "chihou_prod_lgb_win.v12_44feat.txt"
                win_model.save_model(str(win_out))
                logger.info("win モデル保存完了: %s", win_out)
                print(f"  win モデル保存完了: {win_out}")
        else:
            print("  → 採用基準未達。市場乖離特徴の追加効果は確認できず。現行モデル維持。")

    print(f"\n{sep}")
    print("■ 分析完了")
    print("  学習期間ごとに学習→テスト評価。テストデータは学習に一切使用していない。")
    print(sep)


if __name__ == "__main__":
    main()
