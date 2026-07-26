"""v26 Phase1 新規特徴量検証スクリプト（研究用・DB書き込みなし）

既存 v26 LightGBM モデル（`train_v26_lightgbm.py`）の34特徴量（ALL_FEATURES）に、
新規発案の7特徴量を追加した場合に複勝的中率(3着以内)・単勝ROIが改善するかを
単一 hold-out テストで検証する。

新規7特徴量（すべて point-in-time。対象レース日付より厳密に前のデータのみ参照）:
  1. corner_stretch_regression — 直近3走の (4角通過順位 - 確定着順) の平均
  2. bounce_score              — 前走speed_index - その前3走のspeed_index平均（反動リスク）
  3. pace_index_pci            — 直近3走の平均PCI（末脚型/先行型の指標）
  4. collateral_form           — 直近3走の対戦相手の point-in-time 複勝率の平均
  5. nicks_score               — (sire, sire_of_dam) ペアの複勝率（自己参照除外・ベイズ縮小）
  6. peak_weight_proximity     — 自己ベスト着順時の馬体重との近さ
  7. jockey_trainer_combo      — (jockey_id, trainer_id) ペアの複勝率（自己参照除外・ベイズ縮小）

point-in-time 実装は `pandas.merge_asof(direction="backward", allow_exact_matches=False)`
を全特徴量で統一的に使用し、対象レースの日付と厳密に一致するデータは除外する
（当日の他レース結果が混入するリークを防ぐ）。

データ取得は2系統:
  - EXT_DATA_QUERY: 学習/検証/テスト対象（2023-05-01〜2026-04-30, v24指数35特徴量+新規列）
  - HISTORY_QUERY:   新規特徴量の point-in-time 集計用の広範な生履歴（2015-01-01〜2026-04-30）

出力:
  backend/models/v26_phase1_metrics.json — baseline/extended 両方の学習・評価メトリクス
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

load_dotenv(_root.parent / ".env")

from scripts.train_v26_lightgbm import (  # noqa: E402
    ALL_FEATURES,
    V24_VERSION,
    evaluate,
    featurize,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v26_phase1")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# --- 期間定義（train_v26_lightgbm.py と完全に同一） ---
TRAIN_START, TRAIN_END = "20230501", "20250630"
VALID_START, VALID_END = "20250701", "20251231"
TEST_START, TEST_END = "20260101", "20260430"

# --- point-in-time 集計用の広範な履歴期間 ---
HISTORY_START = "20150101"
HISTORY_END = "20260430"

# --- ベイズ縮小の疑似サンプル数 (nicks_score / jockey_trainer_combo 共通) ---
K_SHRINK = 20
MIN_SAMPLE_FOR_SHRINK_NOTE = 20  # ドキュメント用（K_SHRINKと同値）

# --- collateral_form の直近走数。仕様上は3だが、重すぎる場合は1に簡略化してよい ---
COLLATERAL_LOOKBACK = 3

NEW_FEATURES = [
    "corner_stretch_regression",
    "bounce_score",
    "pace_index_pci",
    "collateral_form",
    "nicks_score",
    "peak_weight_proximity",
    "jockey_trainer_combo",
]

EXTENDED_FEATURES = ALL_FEATURES + NEW_FEATURES

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# train_v26_lightgbm.DATA_QUERY と同じ結合構造 + jockey_id/trainer_id/passing_4/
# last_3f/finish_time/sire/sire_of_dam を追加。
EXT_DATA_QUERY = """
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
    -- Phase1 新規特徴量の生入力
    re.jockey_id, re.trainer_id,
    rr.passing_4, rr.last_3f, rr.finish_time,
    p.sire, p.sire_of_dam,
    -- target
    rr.finish_position, rr.win_popularity, rr.win_odds
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
LEFT JOIN keiba.pedigrees p ON p.horse_id = ci.horse_id
WHERE ci.version = %(ver)s
  AND r.head_count >= 8
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10');
"""

# 広範な生履歴クエリ（point-in-time 集計専用）。
# race_results JOIN race_entries JOIN races、abnormality_code=0 のみ。
# pedigrees / calculated_indices(v24) は LEFT JOIN
# （全馬が血統・v24指数を持つわけではないため INNER にすると history が欠落する）。
HISTORY_QUERY = """
SELECT
    rr.race_id, rr.horse_id,
    r.date::int AS date,
    rr.finish_position, rr.passing_4, rr.last_3f, rr.finish_time, rr.horse_weight,
    r.distance,
    re.jockey_id, re.trainer_id,
    p.sire, p.sire_of_dam,
    ci.speed_index
FROM keiba.race_results rr
JOIN keiba.races r ON r.id = rr.race_id
JOIN keiba.race_entries re ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
LEFT JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
LEFT JOIN keiba.calculated_indices ci
    ON ci.race_id = rr.race_id AND ci.horse_id = rr.horse_id AND ci.version = %(ver)s
WHERE COALESCE(rr.abnormality_code, 0) = 0
  AND r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10');
"""


def fetch_dataset(conn: psycopg2.extensions.connection, start: str, end: str) -> pd.DataFrame:
    """対象期間の学習データセットを取得する（既存34特徴量+Phase1新規列の生入力）。"""
    cur = conn.cursor()
    cur.execute(EXT_DATA_QUERY, {"ver": V24_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"[dataset] 取得: {len(df):,}行 ({start}〜{end})")
    return df


def fetch_history(conn: psycopg2.extensions.connection, start: str, end: str) -> pd.DataFrame:
    """point-in-time 集計用の広範な生履歴を取得する（1回のみ）。"""
    cur = conn.cursor()
    cur.execute(HISTORY_QUERY, {"ver": V24_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"[history] 取得: {len(df):,}行 ({start}〜{end})")
    return df


# ---------------------------------------------------------------------------
# 純粋な計算式（単体テスト対象）
# ---------------------------------------------------------------------------


def compute_pci(finish_time: float | None, last_3f: float | None, distance: float | None) -> float | None:
    """単走の PCI（ペース配分指数）を計算する。

    rest_time = finish_time - last_3f（残り(distance-600)mのタイム）
    rest_time_per_3f = rest_time / ((distance - 600) / 600.0)（3F換算）
    PCI = rest_time_per_3f / last_3f * 100 - 50

    Args:
        finish_time: 走破タイム（秒）
        last_3f: 後3ハロンタイム（秒）
        distance: 距離（m）

    Returns:
        PCI値。distance<=600 または last_3f が None/0 の場合は None。
    """
    if distance is None or pd.isna(distance) or distance <= 600:
        return None
    if last_3f is None or pd.isna(last_3f) or last_3f == 0:
        return None
    if finish_time is None or pd.isna(finish_time):
        return None
    rest_time = finish_time - last_3f
    rest_time_per_3f = rest_time / ((distance - 600) / 600.0)
    return rest_time_per_3f / last_3f * 100 - 50


def shrink_rate(n: int | float, rate: float | None, global_rate: float, k: int = K_SHRINK) -> float:
    """ベイズ的縮小: (n*rate + k*global_rate) / (n+k)。

    n=0 または rate が None/NaN の場合は global_rate をそのまま返す。

    Args:
        n: 観測サンプル数
        rate: 観測された複勝率（母集団あり時のみ有効）
        global_rate: 全体平均複勝率（縮小先）
        k: 縮小の強さ（疑似サンプル数）

    Returns:
        縮小後のレート。
    """
    if n is None or n <= 0 or rate is None or pd.isna(rate):
        return global_rate
    return (n * rate + k * global_rate) / (n + k)


# ---------------------------------------------------------------------------
# point-in-time 集計の汎用ヘルパー
# ---------------------------------------------------------------------------


def _daily_cum_table(
    df: pd.DataFrame, group_cols: list[str], value_col: str, date_col: str = "date"
) -> pd.DataFrame:
    """group_cols × date 粒度で value_col の累積和・累積件数（当日含む）テーブルを作る。

    Args:
        df: 入力データフレーム（value_col・date_col・group_cols を含む）
        group_cols: グルーピングキー（空リスト可＝グローバル集計）
        value_col: 集計対象列（NaNは自動的にsum/countから除外される）
        date_col: 日付列名（int YYYYMMDD）

    Returns:
        group_cols + [date_col, "cum_sum_incl", "cum_count_incl"] を持つデータフレーム
        （date_col 時点を含む累積値。ソート済み）。
    """
    keys = group_cols + [date_col]
    daily = df.groupby(keys, dropna=False)[value_col].agg(["sum", "count"]).reset_index()
    daily = daily.sort_values(keys).reset_index(drop=True)
    if group_cols:
        daily["cum_sum_incl"] = daily.groupby(group_cols)["sum"].cumsum()
        daily["cum_count_incl"] = daily.groupby(group_cols)["count"].cumsum()
    else:
        daily["cum_sum_incl"] = daily["sum"].cumsum()
        daily["cum_count_incl"] = daily["count"].cumsum()
    return daily


def _asof_merge(
    target: pd.DataFrame,
    lookup: pd.DataFrame,
    value_cols: list[str],
    group_cols: list[str] | None = None,
    target_date_col: str = "race_date",
    lookup_date_col: str = "date",
) -> pd.DataFrame:
    """merge_asof(direction="backward", allow_exact_matches=False) で point-in-time 結合する。

    target の日付より厳密に前（同日は除外）の lookup 行のうち最も新しいものを採用する。
    未来データが混入しない point-in-time 結合の統一実装。

    Args:
        target: 結合先データフレーム（target_date_col・group_cols を含む）
        lookup: 結合元データフレーム（lookup_date_col・group_cols・value_cols を含む）
        value_cols: 取得したい値の列名
        group_cols: 結合キー（Noneの場合はグループなし＝グローバル結合）
        target_date_col: target 側の日付列
        lookup_date_col: lookup 側の日付列

    Returns:
        target と同じ index を持つ、value_cols のみのデータフレーム（該当なしは NaN）。
    """
    gcols = group_cols or []
    left = target[[target_date_col] + gcols].reset_index()
    # merge_asof は on 列がグローバルにソート済みであることを要求する（by 列は事前ソート不要）
    left = left.sort_values(target_date_col)
    right_cols = gcols + [lookup_date_col] + value_cols
    right = lookup[right_cols].sort_values(lookup_date_col)
    merged = pd.merge_asof(
        left,
        right,
        left_on=target_date_col,
        right_on=lookup_date_col,
        by=group_cols if group_cols else None,
        direction="backward",
        allow_exact_matches=False,
    )
    merged = merged.set_index("index").reindex(target.index)
    return merged[value_cols]


# ---------------------------------------------------------------------------
# Phase1 特徴量本体
# ---------------------------------------------------------------------------


def attach_phase1_features(
    target: pd.DataFrame,
    history: pd.DataFrame,
    collateral_lookback: int = COLLATERAL_LOOKBACK,
) -> pd.DataFrame:
    """target（対象データセット）に Phase1 新規7特徴量を point-in-time で付与する。

    Args:
        target: EXT_DATA_QUERY で取得したデータフレーム（race_date, horse_id 等を含む）
        history: HISTORY_QUERY で取得した広範な生履歴データフレーム
        collateral_lookback: collateral_form の直近走数（デフォルト3、簡略化時は1）

    Returns:
        target に7新規列を追加したコピー。
    """
    target = target.copy()
    history = history.copy()

    for c in ["finish_position", "passing_4", "last_3f", "finish_time", "horse_weight",
              "distance", "speed_index"]:
        history[c] = pd.to_numeric(history[c], errors="coerce")
    history["horse_id"] = history["horse_id"].astype(int)
    history["race_id"] = history["race_id"].astype(int)
    history["date"] = history["date"].astype(int)

    target["horse_id"] = target["horse_id"].astype(int)
    target["race_date"] = pd.to_numeric(target["race_date"], errors="coerce").astype(int)

    is_place = np.where(
        history["finish_position"].notna(), (history["finish_position"] <= 3).astype(float), np.nan
    )
    history["is_place"] = is_place

    # ------------------------------------------------------------------
    # 1. corner_stretch_regression
    # ------------------------------------------------------------------
    filt1 = history.dropna(subset=["passing_4", "finish_position"]).copy()
    filt1["diff"] = filt1["passing_4"] - filt1["finish_position"]
    filt1 = filt1.sort_values(["horse_id", "date"])
    filt1["csr_incl"] = filt1.groupby("horse_id")["diff"].transform(
        lambda s: s.rolling(3, min_periods=1).mean()
    )
    res1 = _asof_merge(target, filt1, ["csr_incl"], group_cols=["horse_id"])
    target["corner_stretch_regression"] = res1["csr_incl"].fillna(0.0)

    # ------------------------------------------------------------------
    # 2. bounce_score
    # ------------------------------------------------------------------
    filt2 = history.dropna(subset=["speed_index"]).copy()
    filt2 = filt2.sort_values(["horse_id", "date"])

    def _bounce(s: pd.Series) -> pd.Series:
        baseline = s.shift(1).rolling(3, min_periods=3).mean()
        return s - baseline

    filt2["bounce_incl"] = filt2.groupby("horse_id")["speed_index"].transform(_bounce)
    res2 = _asof_merge(target, filt2, ["bounce_incl"], group_cols=["horse_id"])
    target["bounce_score"] = res2["bounce_incl"].fillna(0.0)

    # ------------------------------------------------------------------
    # 3. pace_index_pci
    # ------------------------------------------------------------------
    filt3 = history.copy()
    # compute_pci() と同一の式をベクトル化（540k行への行単位applyは遅すぎるため）
    _distance = filt3["distance"]
    _last3f = filt3["last_3f"]
    _ftime = filt3["finish_time"]
    _valid_pci = _distance.notna() & (_distance > 600) & _last3f.notna() & (_last3f != 0) & _ftime.notna()
    _rest_time = _ftime - _last3f
    _rest_per_3f = _rest_time / ((_distance - 600) / 600.0)
    _pci = _rest_per_3f / _last3f * 100 - 50
    filt3["pci"] = np.where(_valid_pci, _pci, np.nan)
    filt3 = filt3.dropna(subset=["pci"]).sort_values(["horse_id", "date"])
    filt3["pci_incl"] = filt3.groupby("horse_id")["pci"].transform(
        lambda s: s.rolling(3, min_periods=1).mean()
    )
    res3 = _asof_merge(target, filt3, ["pci_incl"], group_cols=["horse_id"])
    target["pace_index_pci"] = res3["pci_incl"].fillna(0.0)

    # ------------------------------------------------------------------
    # 6. peak_weight_proximity（先に計算: history 全体を汚さないよう独立コピーで実施）
    # ------------------------------------------------------------------
    filt6 = history.dropna(subset=["finish_position", "horse_weight"]).copy()
    filt6 = filt6.sort_values(["horse_id", "date"])
    running_min_fp = filt6.groupby("horse_id")["finish_position"].cummin()
    prev_min = running_min_fp.groupby(filt6["horse_id"]).shift(1)
    is_new_record = (prev_min.isna()) | (running_min_fp < prev_min)
    record_weight = filt6["horse_weight"].where(is_new_record)
    filt6["peak_weight_incl"] = record_weight.groupby(filt6["horse_id"]).ffill()
    res6 = _asof_merge(target, filt6, ["peak_weight_incl"], group_cols=["horse_id"])
    target["_peak_weight_asof"] = res6["peak_weight_incl"]
    target["peak_weight_proximity"] = np.where(
        target["_peak_weight_asof"].notna(),
        -(target["horse_weight"].astype(float) - target["_peak_weight_asof"]).abs(),
        0.0,
    )
    target = target.drop(columns=["_peak_weight_asof"])

    # ------------------------------------------------------------------
    # 4. collateral_form
    # ------------------------------------------------------------------
    own_daily = _daily_cum_table(history, ["horse_id"], "is_place")
    own_on_hist = _asof_merge(
        history, own_daily, ["cum_sum_incl", "cum_count_incl"],
        group_cols=["horse_id"], target_date_col="date", lookup_date_col="date",
    )
    history["own_place_rate_prior"] = np.where(
        own_on_hist["cum_count_incl"].fillna(0) > 0,
        own_on_hist["cum_sum_incl"] / own_on_hist["cum_count_incl"],
        np.nan,
    )

    race_stats = (
        history.dropna(subset=["own_place_rate_prior"])
        .groupby("race_id")["own_place_rate_prior"]
        .agg(race_sum="sum", race_count="count")
        .reset_index()
    )
    history = history.merge(race_stats, on="race_id", how="left")
    history["race_sum"] = history["race_sum"].fillna(0.0)
    history["race_count"] = history["race_count"].fillna(0)

    own_notna = history["own_place_rate_prior"].notna()
    excl_sum = np.where(
        own_notna, history["race_sum"] - history["own_place_rate_prior"].fillna(0.0), history["race_sum"]
    )
    excl_count = np.where(own_notna, history["race_count"] - 1, history["race_count"])
    opponent_avg = np.where(excl_count > 0, excl_sum / np.maximum(excl_count, 1), np.nan)
    history["race_opponent_avg"] = pd.Series(opponent_avg, index=history.index).fillna(0.5)

    hist_sorted = history.sort_values(["horse_id", "date"]).copy()
    hist_sorted["collateral_incl"] = hist_sorted.groupby("horse_id")["race_opponent_avg"].transform(
        lambda s: s.rolling(collateral_lookback, min_periods=1).mean()
    )
    res4 = _asof_merge(target, hist_sorted, ["collateral_incl"], group_cols=["horse_id"])
    target["collateral_form"] = res4["collateral_incl"].fillna(0.5)

    # ------------------------------------------------------------------
    # グローバル point-in-time 複勝率（5・7 の縮小先）
    # ------------------------------------------------------------------
    global_daily = _daily_cum_table(history, [], "is_place")
    res_g = _asof_merge(target, global_daily, ["cum_sum_incl", "cum_count_incl"], group_cols=None)
    global_rate = np.where(
        res_g["cum_count_incl"].fillna(0) > 0,
        res_g["cum_sum_incl"] / res_g["cum_count_incl"],
        1.0 / 3.0,  # 履歴が全く無い場合のフォールバック（3頭に1頭が複勝）
    )
    target["_global_place_rate"] = global_rate

    # ------------------------------------------------------------------
    # 5. nicks_score
    # ------------------------------------------------------------------
    hist_ped = history.dropna(subset=["sire", "sire_of_dam"]).copy()
    hist_ped["pair_key"] = hist_ped["sire"].astype(str) + "::" + hist_ped["sire_of_dam"].astype(str)
    pair_daily = _daily_cum_table(hist_ped, ["pair_key"], "is_place")

    target["pair_key"] = target["sire"].fillna("__NA__").astype(str) + "::" + target["sire_of_dam"].fillna(
        "__NA__"
    ).astype(str)
    res_pair = _asof_merge(target, pair_daily, ["cum_sum_incl", "cum_count_incl"], group_cols=["pair_key"])
    res_own = _asof_merge(target, own_daily, ["cum_sum_incl", "cum_count_incl"], group_cols=["horse_id"])

    pair_sum = res_pair["cum_sum_incl"].fillna(0.0)
    pair_cnt = res_pair["cum_count_incl"].fillna(0)
    own_sum = res_own["cum_sum_incl"].fillna(0.0)
    own_cnt = res_own["cum_count_incl"].fillna(0)

    nicks_excl_sum = pair_sum - own_sum
    nicks_excl_cnt = (pair_cnt - own_cnt).clip(lower=0)
    nicks_rate = np.where(nicks_excl_cnt > 0, nicks_excl_sum / nicks_excl_cnt.replace(0, np.nan), np.nan)

    target["nicks_score"] = [
        shrink_rate(n, r, g, K_SHRINK)
        for n, r, g in zip(nicks_excl_cnt.values, nicks_rate, target["_global_place_rate"].values)
    ]

    # ------------------------------------------------------------------
    # 7. jockey_trainer_combo
    # ------------------------------------------------------------------
    hist_jt = history.dropna(subset=["jockey_id", "trainer_id"]).copy()
    hist_jt["combo_key"] = (
        hist_jt["jockey_id"].astype(int).astype(str) + "::" + hist_jt["trainer_id"].astype(int).astype(str)
    )
    hist_jt["horse_combo_key"] = hist_jt["horse_id"].astype(str) + "::" + hist_jt["combo_key"]

    combo_daily = _daily_cum_table(hist_jt, ["combo_key"], "is_place")
    own_combo_daily = _daily_cum_table(hist_jt, ["horse_combo_key"], "is_place")

    target["combo_key"] = np.where(
        target["jockey_id"].notna() & target["trainer_id"].notna(),
        target["jockey_id"].fillna(-1).astype(int).astype(str)
        + "::"
        + target["trainer_id"].fillna(-1).astype(int).astype(str),
        "__NA__",
    )
    target["horse_combo_key"] = target["horse_id"].astype(str) + "::" + target["combo_key"]

    res_combo = _asof_merge(target, combo_daily, ["cum_sum_incl", "cum_count_incl"], group_cols=["combo_key"])
    res_own_combo = _asof_merge(
        target, own_combo_daily, ["cum_sum_incl", "cum_count_incl"], group_cols=["horse_combo_key"]
    )

    combo_sum = res_combo["cum_sum_incl"].fillna(0.0)
    combo_cnt = res_combo["cum_count_incl"].fillna(0)
    own_combo_sum = res_own_combo["cum_sum_incl"].fillna(0.0)
    own_combo_cnt = res_own_combo["cum_count_incl"].fillna(0)

    jt_excl_sum = combo_sum - own_combo_sum
    jt_excl_cnt = (combo_cnt - own_combo_cnt).clip(lower=0)
    jt_rate = np.where(jt_excl_cnt > 0, jt_excl_sum / jt_excl_cnt.replace(0, np.nan), np.nan)

    target["jockey_trainer_combo"] = [
        shrink_rate(n, r, g, K_SHRINK)
        for n, r, g in zip(jt_excl_cnt.values, jt_rate, target["_global_place_rate"].values)
    ]

    target = target.drop(
        columns=["_global_place_rate", "pair_key", "combo_key", "horse_combo_key"], errors="ignore"
    )
    return target


# ---------------------------------------------------------------------------
# 学習・評価
# ---------------------------------------------------------------------------


LGB_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "max_depth": 6,
    "min_data_in_leaf": 100,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "learning_rate": 0.05,
    "verbose": -1,
}
NUM_ITERATIONS = 500


def train_and_evaluate(
    df_train: pd.DataFrame, df_valid: pd.DataFrame, df_test: pd.DataFrame, features: list[str], label: str
) -> dict:
    """features を使って LightGBM binary モデルを学習し、train/valid/testを評価する。"""
    X_train = df_train[features].values
    X_valid = df_valid[features].values
    X_test = df_test[features].values
    y_train = df_train["y"].values
    y_valid = df_valid["y"].values

    train_set = lgb.Dataset(X_train, y_train, feature_name=features)
    valid_set = lgb.Dataset(X_valid, y_valid, feature_name=features, reference=train_set)

    logger.info(f"[{label}] 学習開始 n_features={len(features)}")
    model = lgb.train(
        LGB_PARAMS,
        train_set,
        num_boost_round=NUM_ITERATIONS,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(50)],
    )

    s_train = model.predict(X_train, num_iteration=model.best_iteration)
    s_valid = model.predict(X_valid, num_iteration=model.best_iteration)
    s_test = model.predict(X_test, num_iteration=model.best_iteration)

    metrics = {
        "label": label,
        "n_features": len(features),
        "train": evaluate(df_train, s_train, f"{label}-train"),
        "valid": evaluate(df_valid, s_valid, f"{label}-valid"),
        "test": evaluate(df_test, s_test, f"{label}-test"),
    }

    importance = sorted(
        zip(features, model.feature_importance(importance_type="gain")), key=lambda x: -x[1]
    )
    metrics["feature_importance"] = [{"feature": f, "gain": float(g)} for f, g in importance]
    metrics["new_feature_ranks"] = [
        {"feature": f, "rank": i + 1, "gain": float(g)}
        for i, (f, g) in enumerate(importance)
        if f in NEW_FEATURES
    ]

    model.save_model(str(MODELS_DIR / f"v26_phase1_{label}.txt"))
    return metrics


def main() -> None:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    df_train_raw = fetch_dataset(conn, TRAIN_START, TRAIN_END)
    df_valid_raw = fetch_dataset(conn, VALID_START, VALID_END)
    df_test_raw = fetch_dataset(conn, TEST_START, TEST_END)
    history = fetch_history(conn, HISTORY_START, HISTORY_END)
    conn.close()

    df_train = featurize(df_train_raw)
    df_valid = featurize(df_valid_raw)
    df_test = featurize(df_test_raw)

    logger.info("Phase1 新規特徴量を付与中（point-in-time, merge_asof）...")
    df_train = attach_phase1_features(df_train, history)
    df_valid = attach_phase1_features(df_valid, history)
    df_test = attach_phase1_features(df_test, history)
    logger.info("Phase1 新規特徴量の付与完了")

    for c in NEW_FEATURES:
        for df in (df_train, df_valid, df_test):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df_train["y"] = (df_train["finish_position"] <= 3).astype(int)
    df_valid["y"] = (df_valid["finish_position"] <= 3).astype(int)
    df_test["y"] = (df_test["finish_position"] <= 3).astype(int)

    results = {}
    results["baseline"] = train_and_evaluate(df_train, df_valid, df_test, ALL_FEATURES, "baseline")
    results["extended"] = train_and_evaluate(df_train, df_valid, df_test, EXTENDED_FEATURES, "extended")

    # --- 比較表示 ---
    b = results["baseline"]["test"]
    e = results["extended"]["test"]
    logger.info("=" * 70)
    logger.info("baseline vs extended 比較 (test: 2026-01-01〜2026-04-30)")
    logger.info(f"{'metric':<20}{'baseline':>15}{'extended':>15}{'diff':>15}")
    for key in ["n_races", "top1_win_pct", "top1_place_pct", "top1_win_roi"]:
        diff = e[key] - b[key] if isinstance(e[key], (int, float)) else "-"
        logger.info(f"{key:<20}{b[key]:>15}{e[key]:>15}{diff!s:>15}")
    logger.info("=" * 70)
    logger.info("新規特徴量7種の feature importance (gain) 順位:")
    for item in results["extended"]["new_feature_ranks"]:
        logger.info(f"  #{item['rank']:>2}  {item['feature']:<28} gain={item['gain']:.1f}")

    out_path = MODELS_DIR / "v26_phase1_metrics.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "config": {
                    "train": [TRAIN_START, TRAIN_END],
                    "valid": [VALID_START, VALID_END],
                    "test": [TEST_START, TEST_END],
                    "history_range": [HISTORY_START, HISTORY_END],
                    "collateral_lookback": COLLATERAL_LOOKBACK,
                    "k_shrink": K_SHRINK,
                    "new_features": NEW_FEATURES,
                },
                "baseline": results["baseline"],
                "extended": results["extended"],
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    logger.info(f"完了: {out_path}")


if __name__ == "__main__":
    main()
