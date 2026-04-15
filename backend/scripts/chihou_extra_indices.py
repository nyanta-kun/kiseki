"""地方競馬 追加指数計算モジュール

既存の5指数（speed/last3f/jockey/rotation/place_ev）に加えて
9つの新規指数をオンザフライで計算する。
DBのraw dataから直接計算するためバックフィル不要で検証可能。

各関数シグネチャ:
  compute_XXX(df: pd.DataFrame, engine: Engine) -> pd.Series
  - 入力: load_data() + filter_valid() 済みの DataFrame
  - 出力: 同じ index の Series（0-100スケール, NULL→50.0）

指数一覧:
  1. frame_bias    - 枠順バイアス（コース×距離帯×枠番の3着内率）
  2. pace_fit      - 脚質展開適性（出走メンバーの脚質分布 vs 各馬の脚質傾向）
  3. last_margin   - 前走着差（1着とのタイム差）
  4. trainer       - 調教師指数（勝率×ROI加重）
  5. weight_trend  - 馬体重変化（前走比増減スコア）
  6. jockey_course - 騎手×競馬場適性指数
  7. distance_apt  - 距離適性指数（距離帯別着順）
  8. track_cond    - 馬場状態適性指数
  9. pedigree_local- 血統地方適性（父馬×競馬場）
"""
from __future__ import annotations

import logging
from bisect import bisect_left
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger("chihou_extra_indices")

BANEI_COURSE = "83"
_MIN_SAMPLES = 20  # 統計の最低サンプル数（以下はフォールバック）


# ─────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────

def _z_norm(series: pd.Series, mean: float = 50.0, std: float = 10.0) -> pd.Series:
    """Z正規化して0-100スケールに変換。NaN → mean で補完。"""
    s = series.dropna()
    if len(s) < 2:
        return pd.Series(mean, index=series.index)
    mu = float(s.mean())
    sigma = float(s.std())
    if sigma < 1e-9:
        return pd.Series(mean, index=series.index)
    result = (series - mu) / sigma * std + mean
    return result.clip(0.0, 100.0).fillna(mean)


def _get_entries(race_ids: list, engine: "Engine") -> pd.DataFrame:
    """race_entries から (race_id, horse_id, frame_number, jockey_id, trainer_id) を取得する。"""
    sql = text("""
        SELECT race_id, horse_id, frame_number, jockey_id, trainer_id
        FROM chihou.race_entries
        WHERE race_id = ANY(:rids)
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rids": race_ids}).fetchall()
    if not rows:
        return pd.DataFrame(columns=["race_id", "horse_id", "frame_number", "jockey_id", "trainer_id"])
    return pd.DataFrame(rows, columns=["race_id", "horse_id", "frame_number", "jockey_id", "trainer_id"])


# ─────────────────────────────────────────────────────────────────
# 1. 枠順バイアス指数 (frame_bias)
# ─────────────────────────────────────────────────────────────────

def compute_frame_bias(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """コース × 距離帯 × 枠番の歴史的3着内率をスコア化する。

    サンプル不足の場合: コース×枠番 → 全体枠番平均 の順でフォールバック。
    ばんえい競馬（course='83'）は除外。
    """
    # 全歴史的枠番統計（ばんえい除外）
    stats_sql = text("""
        SELECT
            r.course,
            CASE WHEN r.distance <= 1200 THEN 'S'
                 WHEN r.distance <= 1800 THEN 'M'
                 ELSE 'L' END AS dist_band,
            rr.frame_number,
            COUNT(*) AS total,
            SUM(CASE WHEN rr.finish_position <= 3 THEN 1 ELSE 0 END) AS top3
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND rr.frame_number IS NOT NULL
          AND r.course != :banei
        GROUP BY r.course, dist_band, rr.frame_number
    """)

    race_ids = df["race_id"].unique().tolist()

    with engine.connect() as conn:
        stats = pd.DataFrame(
            conn.execute(stats_sql, {"banei": BANEI_COURSE}).fetchall(),
            columns=["course", "dist_band", "frame_number", "total", "top3"],
        )

    entries = _get_entries(race_ids, engine)

    if stats.empty or entries.empty:
        return pd.Series(50.0, index=df.index, name="frame_bias")

    stats["top3_rate"] = stats["top3"].astype(float) / stats["total"].astype(float)

    # 精細統計（course × dist_band × frame_number）
    fine = stats[stats["total"] >= _MIN_SAMPLES][
        ["course", "dist_band", "frame_number", "top3_rate"]
    ].copy()

    # フォールバック1: course × frame_number
    coarse = (
        stats.groupby(["course", "frame_number"])["top3_rate"]
        .mean()
        .reset_index()
        .rename(columns={"top3_rate": "top3_rate_c"})
    )

    # フォールバック2: 全体 frame_number 平均
    global_fn = (
        stats.groupby("frame_number")["top3_rate"]
        .mean()
        .reset_index()
        .rename(columns={"top3_rate": "top3_rate_g"})
    )

    work = df[["race_id", "horse_id", "course", "distance"]].copy()
    work = work.merge(entries[["race_id", "horse_id", "frame_number"]], on=["race_id", "horse_id"], how="left")
    work["dist_band"] = pd.cut(
        work["distance"], bins=[0, 1200, 1800, 99999], labels=["S", "M", "L"]
    ).astype(str)

    work = work.merge(fine, on=["course", "dist_band", "frame_number"], how="left")
    work = work.merge(coarse, on=["course", "frame_number"], how="left")
    work = work.merge(global_fn, on="frame_number", how="left")

    work["rate"] = (
        work["top3_rate"]
        .fillna(work["top3_rate_c"])
        .fillna(work["top3_rate_g"])
    )

    result = _z_norm(work["rate"])
    result.index = df.index
    return result.rename("frame_bias")


# ─────────────────────────────────────────────────────────────────
# 2. 脚質展開適性指数 (pace_fit)
# ─────────────────────────────────────────────────────────────────

def compute_pace_fit(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """各馬の脚質傾向 × 出走メンバーの脚質分布から展開適性をスコア化する。

    h = horse_frontrunner_rate  (1=逃げ, 0=追い込み)
    r = race_frontrunner_avg    (メンバー平均)
    pace_fit = h*(1-r) + (1-h)*r  (対角が高得点)
    """
    horse_ids = df["horse_id"].unique().tolist()

    style_sql = text("""
        SELECT rr.horse_id, rr.running_style
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE rr.horse_id = ANY(:hids)
          AND rr.running_style IS NOT NULL
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND r.course != :banei
    """)

    with engine.connect() as conn:
        style_rows = pd.DataFrame(
            conn.execute(style_sql, {"hids": horse_ids, "banei": BANEI_COURSE}).fetchall(),
            columns=["horse_id", "running_style"],
        )

    if style_rows.empty:
        return pd.Series(50.0, index=df.index, name="pace_fit")

    style_rows["running_style"] = pd.to_numeric(style_rows["running_style"], errors="coerce")
    # front_rate: 1=逃(1)/先(2), 0=差(3)/追(4) → (4 - style) / 3 で0-1スケール
    style_rows["front_score"] = (4.0 - style_rows["running_style"]) / 3.0

    # 各馬の歴史的front_rate平均
    horse_front = style_rows.groupby("horse_id")["front_score"].mean().rename("horse_front_rate")

    work = df[["race_id", "horse_id"]].copy()
    work = work.merge(horse_front, on="horse_id", how="left")
    work["horse_front_rate"] = work["horse_front_rate"].fillna(0.5)  # 不明→中間

    # レース内のfront_rate平均
    race_front_avg = work.groupby("race_id")["horse_front_rate"].mean().rename("race_front_avg")
    work = work.merge(race_front_avg, on="race_id", how="left")

    h = work["horse_front_rate"]
    r = work["race_front_avg"]
    # 対角スコア: 逃げ馬は後傾レースで有利, 差し馬は前傾レースで有利
    work["pace_fit_raw"] = h * (1 - r) + (1 - h) * r

    result = _z_norm(work["pace_fit_raw"])
    result.index = df.index
    return result.rename("pace_fit")


# ─────────────────────────────────────────────────────────────────
# 3. 前走着差指数 (last_margin)
# ─────────────────────────────────────────────────────────────────

def compute_last_margin(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """前走1着とのタイム差（time_diff: 秒）をスコア化する。

    小さいほど良い（接戦=高評価）。1着馬は0秒。
    ルックアヘッドを避けるため target_date より前の直近レースを参照。
    """
    horse_ids = df["horse_id"].unique().tolist()

    hist_sql = text("""
        SELECT rr.horse_id, r.date, rr.time_diff, rr.finish_position
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE rr.horse_id = ANY(:hids)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.course != :banei
        ORDER BY rr.horse_id, r.date
    """)

    with engine.connect() as conn:
        hist = pd.DataFrame(
            conn.execute(hist_sql, {"hids": horse_ids, "banei": BANEI_COURSE}).fetchall(),
            columns=["horse_id", "date", "time_diff", "finish_position"],
        )

    if hist.empty:
        return pd.Series(50.0, index=df.index, name="last_margin")

    hist["time_diff"] = pd.to_numeric(hist["time_diff"], errors="coerce")

    # horse_id → [(date, time_diff, finish_position), ...] (日付昇順)
    horse_hist: dict[int, list[tuple]] = {}
    for row in hist.itertuples(index=False):
        hid = int(row.horse_id)
        if hid not in horse_hist:
            horse_hist[hid] = []
        horse_hist[hid].append((str(row.date), row.time_diff, int(row.finish_position)))

    results = []
    for idx in df.index:
        horse_id = int(df.at[idx, "horse_id"])
        target_date = str(df.at[idx, "date"])
        races = horse_hist.get(horse_id, [])
        if not races:
            results.append(None)
            continue
        dates = [r[0] for r in races]
        pos = bisect_left(dates, target_date)
        if pos == 0:
            results.append(None)
            continue
        prev = races[pos - 1]
        # 1着なら time_diff=0
        if prev[2] == 1:
            results.append(0.0)
        elif pd.notna(prev[1]):
            results.append(float(prev[1]))
        else:
            results.append(None)

    raw = pd.Series(results, index=df.index, dtype="float64")
    # time_diff小=良 → 符号反転（-値が高スコア）
    median_val = float(raw.median()) if raw.notna().any() else 5.0
    neg = -raw.fillna(median_val)

    return _z_norm(neg).rename("last_margin")


# ─────────────────────────────────────────────────────────────────
# 4. 調教師指数 (trainer)
# ─────────────────────────────────────────────────────────────────

def compute_trainer(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """調教師の勝率 × ROI加重スコアを計算する（騎手指数と同様の算出方法）。

    騎手指数との差別化: コース・距離を問わない総合的な「仕上げ能力」を評価。
    """
    race_ids = df["race_id"].unique().tolist()
    entries = _get_entries(race_ids, engine)

    if entries.empty or entries["trainer_id"].isna().all():
        return pd.Series(50.0, index=df.index, name="trainer")

    trainer_ids = entries["trainer_id"].dropna().astype(int).unique().tolist()

    stats_sql = text("""
        SELECT
            re.trainer_id,
            COUNT(*) AS total,
            SUM(CASE WHEN rr.finish_position = 1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN rr.finish_position = 1 THEN rr.win_odds ELSE 0 END) AS win_odds_sum
        FROM chihou.race_results rr
        JOIN chihou.race_entries re ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE re.trainer_id = ANY(:tids)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.course != :banei
        GROUP BY re.trainer_id
        HAVING COUNT(*) >= :min_s
    """)

    with engine.connect() as conn:
        stats = pd.DataFrame(
            conn.execute(stats_sql, {"tids": trainer_ids, "banei": BANEI_COURSE, "min_s": _MIN_SAMPLES}).fetchall(),
            columns=["trainer_id", "total", "wins", "win_odds_sum"],
        )

    if stats.empty:
        return pd.Series(50.0, index=df.index, name="trainer")

    stats["win_rate"] = stats["wins"].astype(float) / stats["total"].astype(float)
    stats["roi"] = stats["win_odds_sum"].astype(float) / stats["total"].astype(float) * 100.0
    # スコア = 勝率z + ROI z の平均
    stats["win_rate_z"] = _z_norm(stats["win_rate"])
    stats["roi_z"] = _z_norm(stats["roi"])
    stats["trainer_score"] = (stats["win_rate_z"] + stats["roi_z"]) / 2.0
    stats["trainer_id"] = stats["trainer_id"].astype(int)

    entries_valid = entries[entries["trainer_id"].notna()].copy()
    entries_valid["trainer_id"] = entries_valid["trainer_id"].astype(int)

    work = df[["race_id", "horse_id"]].copy()
    work = work.merge(entries_valid[["race_id", "horse_id", "trainer_id"]], on=["race_id", "horse_id"], how="left")
    work = work.merge(stats[["trainer_id", "trainer_score"]], on="trainer_id", how="left")

    result = work["trainer_score"].fillna(50.0)
    result.index = df.index
    return result.rename("trainer")


# ─────────────────────────────────────────────────────────────────
# 5. 馬体重変化指数 (weight_trend)
# ─────────────────────────────────────────────────────────────────

def compute_weight_trend(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """前走比体重変化（weight_change）をスコア化する。

    +2〜+6kg: 充実の増量（加点）
    -8kg以上の大幅減: 調子落ちシグナル（減点）
    ±0: 中立
    """
    race_ids = df["race_id"].unique().tolist()

    wc_sql = text("""
        SELECT race_id, horse_id, weight_change
        FROM chihou.race_results
        WHERE race_id = ANY(:rids)
    """)

    with engine.connect() as conn:
        wc = pd.DataFrame(
            conn.execute(wc_sql, {"rids": race_ids}).fetchall(),
            columns=["race_id", "horse_id", "weight_change"],
        )

    if wc.empty:
        return pd.Series(50.0, index=df.index, name="weight_trend")

    wc["weight_change"] = pd.to_numeric(wc["weight_change"], errors="coerce")

    work = df[["race_id", "horse_id"]].copy()
    work = work.merge(wc, on=["race_id", "horse_id"], how="left")

    # 非線形スコアリング: +2〜+6を最高評価、-8以下を最低評価
    # 連続関数: ガウシアン中心+4, sigma=8
    mu, sigma = 4.0, 8.0
    work["wt_raw"] = np.exp(-0.5 * ((work["weight_change"] - mu) / sigma) ** 2)

    result = _z_norm(work["wt_raw"])
    result.index = df.index
    return result.rename("weight_trend")


# ─────────────────────────────────────────────────────────────────
# 6. 騎手×競馬場適性指数 (jockey_course)
# ─────────────────────────────────────────────────────────────────

def compute_jockey_course(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """騎手 × 競馬場の組み合わせ別勝率をスコア化する。

    サンプル不足の場合は騎手全体平均にフォールバック。
    地方騎手の得意/不得意コースを捉える。
    """
    race_ids = df["race_id"].unique().tolist()
    entries = _get_entries(race_ids, engine)

    if entries.empty or entries["jockey_id"].isna().all():
        return pd.Series(50.0, index=df.index, name="jockey_course")

    jockey_ids = entries["jockey_id"].dropna().astype(int).unique().tolist()

    stats_sql = text("""
        SELECT
            re.jockey_id,
            r.course,
            COUNT(*) AS total,
            SUM(CASE WHEN rr.finish_position = 1 THEN 1 ELSE 0 END) AS wins
        FROM chihou.race_results rr
        JOIN chihou.race_entries re ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE re.jockey_id = ANY(:jids)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.course != :banei
        GROUP BY re.jockey_id, r.course
    """)

    with engine.connect() as conn:
        stats = pd.DataFrame(
            conn.execute(stats_sql, {"jids": jockey_ids, "banei": BANEI_COURSE}).fetchall(),
            columns=["jockey_id", "course", "total", "wins"],
        )

    if stats.empty:
        return pd.Series(50.0, index=df.index, name="jockey_course")

    stats["win_rate"] = stats["wins"].astype(float) / stats["total"].astype(float)

    # 精細統計（jockey × course）: MIN_SAMPLES 以上
    fine = stats[stats["total"] >= _MIN_SAMPLES][["jockey_id", "course", "win_rate"]].copy()

    # フォールバック: jockey 全体平均
    jockey_avg = (
        stats.groupby("jockey_id")
        .apply(lambda g: g["wins"].sum() / g["total"].sum(), include_groups=False)
        .reset_index()
        .rename(columns={0: "win_rate_j"})
    )

    entries_valid = entries[entries["jockey_id"].notna()].copy()
    entries_valid["jockey_id"] = entries_valid["jockey_id"].astype(int)

    work = df[["race_id", "horse_id", "course"]].copy()
    work = work.merge(entries_valid[["race_id", "horse_id", "jockey_id"]], on=["race_id", "horse_id"], how="left")
    work = work.merge(fine, on=["jockey_id", "course"], how="left")
    work = work.merge(jockey_avg, on="jockey_id", how="left")
    work["rate"] = work["win_rate"].fillna(work["win_rate_j"])

    result = _z_norm(work["rate"])
    result.index = df.index
    return result.rename("jockey_course")


# ─────────────────────────────────────────────────────────────────
# 7. 距離適性指数 (distance_apt)
# ─────────────────────────────────────────────────────────────────

def compute_distance_apt(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """馬の距離帯別着順から距離適性をスコア化する。

    現在のレース距離帯（S/M/L）での過去成績を使用。
    サンプル不足の場合は全距離平均にフォールバック。
    """
    horse_ids = df["horse_id"].unique().tolist()

    stats_sql = text("""
        SELECT
            rr.horse_id,
            CASE WHEN r.distance <= 1200 THEN 'S'
                 WHEN r.distance <= 1800 THEN 'M'
                 ELSE 'L' END AS dist_band,
            COUNT(*) AS total,
            SUM(CASE WHEN rr.finish_position = 1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN rr.finish_position <= 3 THEN 1 ELSE 0 END) AS top3
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE rr.horse_id = ANY(:hids)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.course != :banei
        GROUP BY rr.horse_id, dist_band
    """)

    with engine.connect() as conn:
        stats = pd.DataFrame(
            conn.execute(stats_sql, {"hids": horse_ids, "banei": BANEI_COURSE}).fetchall(),
            columns=["horse_id", "dist_band", "total", "wins", "top3"],
        )

    if stats.empty:
        return pd.Series(50.0, index=df.index, name="distance_apt")

    stats["top3_rate"] = stats["top3"].astype(float) / stats["total"].astype(float)

    fine = stats[stats["total"] >= 5][["horse_id", "dist_band", "top3_rate"]].copy()
    horse_avg = (
        stats.groupby("horse_id")
        .apply(lambda g: g["top3"].sum() / g["total"].sum(), include_groups=False)
        .reset_index()
        .rename(columns={0: "top3_rate_h"})
    )

    work = df[["race_id", "horse_id", "distance"]].copy()
    work["dist_band"] = pd.cut(
        work["distance"], bins=[0, 1200, 1800, 99999], labels=["S", "M", "L"]
    ).astype(str)
    work = work.merge(fine, on=["horse_id", "dist_band"], how="left")
    work = work.merge(horse_avg, on="horse_id", how="left")
    work["rate"] = work["top3_rate"].fillna(work["top3_rate_h"])

    result = _z_norm(work["rate"])
    result.index = df.index
    return result.rename("distance_apt")


# ─────────────────────────────────────────────────────────────────
# 8. 馬場状態適性指数 (track_cond)
# ─────────────────────────────────────────────────────────────────

def compute_track_cond(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """馬場状態（良/稍/重/不）別の過去成績から道悪・良馬場適性をスコア化する。

    現在のレースの condition に対する各馬の過去成績で評価。
    """
    horse_ids = df["horse_id"].unique().tolist()

    stats_sql = text("""
        SELECT
            rr.horse_id,
            r.condition,
            COUNT(*) AS total,
            SUM(CASE WHEN rr.finish_position <= 3 THEN 1 ELSE 0 END) AS top3
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE rr.horse_id = ANY(:hids)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.condition IS NOT NULL
          AND r.course != :banei
        GROUP BY rr.horse_id, r.condition
    """)

    with engine.connect() as conn:
        stats = pd.DataFrame(
            conn.execute(stats_sql, {"hids": horse_ids, "banei": BANEI_COURSE}).fetchall(),
            columns=["horse_id", "condition", "total", "top3"],
        )

    if stats.empty or "condition" not in df.columns:
        return pd.Series(50.0, index=df.index, name="track_cond")

    stats["top3_rate"] = stats["top3"].astype(float) / stats["total"].astype(float)

    fine = stats[stats["total"] >= 3][["horse_id", "condition", "top3_rate"]].copy()
    horse_avg = (
        stats.groupby("horse_id")
        .apply(lambda g: g["top3"].sum() / g["total"].sum(), include_groups=False)
        .reset_index()
        .rename(columns={0: "top3_rate_h"})
    )

    work = df[["race_id", "horse_id", "condition"]].copy()
    work = work.merge(fine, on=["horse_id", "condition"], how="left")
    work = work.merge(horse_avg, on="horse_id", how="left")
    work["rate"] = work["top3_rate"].fillna(work["top3_rate_h"])

    result = _z_norm(work["rate"])
    result.index = df.index
    return result.rename("track_cond")


# ─────────────────────────────────────────────────────────────────
# 9. 血統地方適性指数 (pedigree_local)
# ─────────────────────────────────────────────────────────────────

def compute_pedigree_local(df: pd.DataFrame, engine: "Engine") -> pd.Series:
    """父馬 × 競馬場の組み合わせ別勝率をスコア化する。

    地方競馬での砂・小回り適性を血統（父馬系統）から評価。
    サンプル不足の場合は父馬全体平均にフォールバック。
    """
    horse_ids = df["horse_id"].unique().tolist()

    # 対象馬の血統情報取得
    ped_sql = text("""
        SELECT horse_id, sire
        FROM chihou.pedigrees
        WHERE horse_id = ANY(:hids) AND sire IS NOT NULL
    """)

    with engine.connect() as conn:
        ped = pd.DataFrame(
            conn.execute(ped_sql, {"hids": horse_ids}).fetchall(),
            columns=["horse_id", "sire"],
        )

    if ped.empty:
        return pd.Series(50.0, index=df.index, name="pedigree_local")

    sire_names = ped["sire"].unique().tolist()

    # 父馬×コース別成績
    stats_sql = text("""
        SELECT
            p.sire,
            r.course,
            COUNT(*) AS total,
            SUM(CASE WHEN rr.finish_position = 1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN rr.finish_position <= 3 THEN 1 ELSE 0 END) AS top3
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        JOIN chihou.pedigrees p ON p.horse_id = rr.horse_id
        WHERE p.sire = ANY(:sires)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.course != :banei
        GROUP BY p.sire, r.course
    """)

    with engine.connect() as conn:
        stats = pd.DataFrame(
            conn.execute(stats_sql, {"sires": sire_names, "banei": BANEI_COURSE}).fetchall(),
            columns=["sire", "course", "total", "wins", "top3"],
        )

    if stats.empty:
        return pd.Series(50.0, index=df.index, name="pedigree_local")

    stats["top3_rate"] = stats["top3"].astype(float) / stats["total"].astype(float)

    fine = stats[stats["total"] >= _MIN_SAMPLES][["sire", "course", "top3_rate"]].copy()
    sire_avg = (
        stats.groupby("sire")
        .apply(lambda g: g["top3"].sum() / g["total"].sum(), include_groups=False)
        .reset_index()
        .rename(columns={0: "top3_rate_s"})
    )

    work = df[["race_id", "horse_id", "course"]].copy()
    work = work.merge(ped, on="horse_id", how="left")
    work = work.merge(fine, on=["sire", "course"], how="left")
    work = work.merge(sire_avg, on="sire", how="left")
    work["rate"] = work["top3_rate"].fillna(work["top3_rate_s"])

    result = _z_norm(work["rate"])
    result.index = df.index
    return result.rename("pedigree_local")


# ─────────────────────────────────────────────────────────────────
# レジストリ
# ─────────────────────────────────────────────────────────────────

EXTRA_INDEX_REGISTRY: dict[str, object] = {
    "frame_bias":    compute_frame_bias,
    "pace_fit":      compute_pace_fit,
    "last_margin":   compute_last_margin,
    "trainer":       compute_trainer,
    "weight_trend":  compute_weight_trend,
    "jockey_course": compute_jockey_course,
    "distance_apt":  compute_distance_apt,
    "track_cond":    compute_track_cond,
    "pedigree_local": compute_pedigree_local,
}

EXTRA_INDEX_LABELS = {
    "frame_bias":    "枠順バイアス",
    "pace_fit":      "脚質展開",
    "last_margin":   "前走着差",
    "trainer":       "調教師",
    "weight_trend":  "体重変化",
    "jockey_course": "騎手×競馬場",
    "distance_apt":  "距離適性",
    "track_cond":    "馬場適性",
    "pedigree_local": "血統地方適性",
}
