"""地方競馬 LightGBM 学習スクリプト（ベース特徴量セット定義 + 学習基盤）

⚠️ 本番モデルの更新はこのスクリプト単体では行われない。
   本番（chihou_calculator.py）がロードするのは v12_44feat モデル
   (models/chihou_prod_lgb.v12_44feat.txt / chihou_prod_lgb_win.v12_44feat.txt) で、
   これを学習・保存するのは scripts/train_chihou_market_lgb.py（本スクリプトの
   FEATURES/prep を import し、市場乖離5特徴を追加して44特徴で学習する）。
   本スクリプトを単体実行すると無サフィックスの models/chihou_prod_lgb.txt を
   上書きするが、そのファイルは本番からロードされない（旧世代の遺物）。

役割:
  - ベース特徴量セット（FEATURES: base17 + 履歴4 + 外部5 + 馬場4 + CT9 = 39）と
    前処理（prep / add_historical_features）の定義元
  - 履歴系特徴は calculate_and_save 内の _history_features_batch と同一意味論
    （train/serve 整合）

2ヘッド構成（Phase2: 単複ヘッド分離＋確率較正）:
  - is_top3 ヘッド → composite ランキング & place_probability
  - is_win  ヘッド → win_probability(較正済)。生 binary 出力がほぼ完璧に
    較正される(Phase2: win ECE 0.0024)ため isotonic は不要。

本番モデル再学習の手順:
  cd backend
  .venv/bin/python scripts/train_chihou_market_lgb.py   # ← 本番 v12 モデルを更新

（本スクリプト単体の実行はベースモデルの実験用途のみ）
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
# Phase6(2026-07-07): コーナー位置/脚質・調教師成績・乗替 9特徴。
# A/B(5seed×2cutoff 決定論OOS, scripts/ab_chihou_corner_trainer.py):
# +all で top1勝率 +0.66/+0.85pt・複勝率 +0.59/+0.68pt(両cutoff全seed改善・★std超)。
# 全て point-in-time(現走前の履歴のみ)・リークなし。serve 側は
# chihou_calculator._corner_features_batch / _trainer_features_batch と同意味論にすること。
#   c_early_n     : 過去走の序盤コーナー位置/頭数の平均 (0=先頭, 欠損→0.5)
#   c_late_gain_n : 過去走の (最終コーナー位置−着順)/頭数 の平均 (+=末脚, 欠損→0)
#   c_makuri_n    : 過去走の (序盤−最終コーナー)/頭数 の平均 (+=まくり, 欠損→0)
#   c_runs        : コーナー有効走数 min(n,20)/20
#   front_density : レース内の先行型(c_early_n≤0.3 かつ 経験あり)割合
#   tr_win_rate   : 調教師 平滑化勝率 (wins+0.08*30)/(runs+30)・前日までの累積
#   tr_top3_rate  : 調教師 平滑化複勝率 (top3+0.25*30)/(runs+30)
#   tr_runs_n     : min(runs,1000)/1000
#   jk_change     : 前走騎手と異なる=1 (初出走→0)
CORNER_FEATURES = ["c_early_n", "c_late_gain_n", "c_makuri_n", "c_runs", "front_density"]
TRAINER_FEATURES = ["tr_win_rate", "tr_top3_rate", "tr_runs_n"]
JKCHG_FEATURES = ["jk_change"]
CT_FEATURES = CORNER_FEATURES + TRAINER_FEATURES + JKCHG_FEATURES
FEATURES = FEATURES + HIST_FEATURES + EXT_FEATURES + TRACK_FEATURES + CT_FEATURES

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
FROM (
    -- v9 優先・なければ最新 version のサブ指数を使う。
    -- v9 バックフィルは 2026-05-04 で停止しており、それ以降は本番 live 行(v10+)で補完する。
    -- サブ指数の計算式は v9 以降不変（v9 vs v10 重複 32万行で jockey/rotation/margin 完全一致・
    -- speed/last3f は par_time 算出時点差の平均0.23のみ。2026-07-07 検証）。
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


HIST_FULL_QUERY = """
SELECT rr.horse_id, r.id AS race_id, r.date, r.head_count, rr.finish_position,
       rr.passing_1, rr.passing_2, rr.passing_3, rr.passing_4,
       rr.jockey_id, re.trainer_id
FROM chihou.race_results rr
JOIN chihou.races r ON r.id = rr.race_id
LEFT JOIN chihou.race_entries re
  ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
WHERE r.course != '83'
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY rr.horse_id, r.date, r.id
"""


def fetch_hist_full(conn) -> pd.DataFrame:
    """コーナー/調教師/乗替特徴算出用の全履歴（passing/jockey/trainer 込み）。"""
    cur = conn.cursor()
    cur.execute(HIST_FULL_QUERY)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def compute_corner_table(hist: pd.DataFrame) -> pd.DataFrame:
    """(horse_id, race_id) → コーナー特徴4 + jk_change。現走前の累積のみ使用。

    serve 側 chihou_calculator._corner_features_batch と同一意味論にすること。
    """
    h = hist.copy()
    for c in ("passing_1", "passing_2", "passing_3", "passing_4",
              "finish_position", "head_count"):
        h[c] = pd.to_numeric(h[c], errors="coerce")
    early = h["passing_2"].fillna(h["passing_1"]).fillna(h["passing_3"])
    late = h["passing_4"].fillna(h["passing_3"])
    hc = h["head_count"].clip(lower=2)
    h["early_n"] = ((early - 1) / (hc - 1)).clip(0, 1)
    h["late_gain"] = ((late - h["finish_position"]) / hc).clip(-1, 1)
    h["makuri"] = ((early - late) / hc).clip(-1, 1)
    h["valid"] = (early.notna() & late.notna()).astype(float)

    h = h.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    g = h.groupby("horse_id")
    for src, dst in (("early_n", "c_early_n"), ("late_gain", "c_late_gain_n"),
                     ("makuri", "c_makuri_n")):
        v = h[src].fillna(0.0) * h["valid"]
        cnt = g["valid"].cumsum() - h["valid"]
        s = v.groupby(h["horse_id"]).cumsum() - v
        h[dst] = s / cnt.clip(lower=1)
        h.loc[cnt < 1, dst] = np.nan
    cnt = g["valid"].cumsum() - h["valid"]
    h["c_runs"] = cnt.clip(upper=20) / 20.0

    prev_jk = g["jockey_id"].shift(1)
    h["jk_change"] = ((prev_jk.notna()) & (prev_jk != h["jockey_id"])).astype(float)

    return h.set_index(["horse_id", "race_id"])[
        ["c_early_n", "c_late_gain_n", "c_makuri_n", "c_runs", "jk_change"]]


def compute_trainer_table(hist: pd.DataFrame) -> pd.DataFrame:
    """(trainer_id, date) → 前日までの累積成績（当日レース間リーク回避）。

    serve 側 chihou_calculator._trainer_features_batch と同一意味論にすること。
    """
    h = hist[hist["trainer_id"].notna()].copy()
    h["fp"] = pd.to_numeric(h["finish_position"], errors="coerce")
    h["win"] = (h["fp"] == 1).astype(float)
    h["top3"] = (h["fp"] <= 3).astype(float)
    day = (h.groupby(["trainer_id", "date"])
             .agg(runs=("fp", "size"), wins=("win", "sum"), top3s=("top3", "sum"))
             .reset_index()
             .sort_values(["trainer_id", "date"]))
    g = day.groupby("trainer_id")
    day["cum_runs"] = g["runs"].cumsum() - day["runs"]
    day["cum_wins"] = g["wins"].cumsum() - day["wins"]
    day["cum_top3"] = g["top3s"].cumsum() - day["top3s"]
    k = 30.0
    day["tr_win_rate"] = (day["cum_wins"] + 0.08 * k) / (day["cum_runs"] + k)
    day["tr_top3_rate"] = (day["cum_top3"] + 0.25 * k) / (day["cum_runs"] + k)
    day["tr_runs_n"] = day["cum_runs"].clip(upper=1000) / 1000.0
    return day.set_index(["trainer_id", "date"])[
        ["tr_win_rate", "tr_top3_rate", "tr_runs_n"]]


def build_ct_tables(conn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """コーナー/調教師特徴テーブル一式を構築する（prep から利用）。"""
    hist_full = fetch_hist_full(conn)
    corner_tbl = compute_corner_table(hist_full)
    trainer_tbl = compute_trainer_table(hist_full)
    trainer_map = hist_full[["horse_id", "race_id", "trainer_id"]].drop_duplicates()
    return corner_tbl, trainer_tbl, trainer_map


def add_corner_trainer_features(df: pd.DataFrame, corner_tbl: pd.DataFrame,
                                trainer_tbl: pd.DataFrame,
                                trainer_map: pd.DataFrame) -> pd.DataFrame:
    """Phase6 の9特徴を付与する（train/serve 整合・欠損は中立値）。"""
    df = df.copy()
    df = df.join(corner_tbl, on=["horse_id", "race_id"], how="left")
    df["c_early_n"] = df["c_early_n"].fillna(0.5)
    for c in ("c_late_gain_n", "c_makuri_n", "c_runs", "jk_change"):
        df[c] = df[c].fillna(0.0)
    is_front = ((df["c_early_n"] <= 0.3) & (df["c_runs"] > 0)).astype(float)
    df["front_density"] = is_front.groupby(df["race_id"]).transform("mean")

    df = df.merge(trainer_map, on=["horse_id", "race_id"], how="left")
    df = df.join(trainer_tbl, on=["trainer_id", "date"], how="left")
    df["tr_win_rate"] = df["tr_win_rate"].fillna(0.08)
    df["tr_top3_rate"] = df["tr_top3_rate"].fillna(0.25)
    df["tr_runs_n"] = df["tr_runs_n"].fillna(0.0)
    return df


def prep(conn, df_raw: pd.DataFrame, df_hist: pd.DataFrame) -> pd.DataFrame:
    """featurize + 履歴系4特徴 + 外部指数特徴 + 馬場特徴 + CT9特徴 + 欠損補完（train/serve 整合）。"""
    df = featurize(df_raw)
    df = add_historical_features(df, df_hist)
    for col in HIST_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1.0)
    df = add_external_features(df)
    df = add_track_features(df, compute_wet_apt_table(fetch_hist_cond(conn)))
    df = add_corner_trainer_features(df, *build_ct_tables(conn))
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
