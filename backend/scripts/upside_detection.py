"""指数下位馬券検出・穴馬パターン分析スクリプト

指数下位（composite_index ランク4位以降）で3着以内に入った馬の
パターンを抽出し、穴馬を事前検出するための特徴量・ルールを導出する。

分析内容:
  1. 指数下位馬券ケースの抽出（乖離スコア算出）
  2. 個別指数の突出パターン検出（どの指数が有効だったか）
  3. 条件別分析（馬場状態・距離・グレード・競馬場）
  4. 穴馬スコア (UpsideScore) の試験的算出
  5. バックテスト的中率・回収率の検証

使い方:
  uv run python scripts/upside_detection.py --start 20240101 --end 20261231
  uv run python scripts/upside_detection.py --start 20250101 --end 20261231 --min-index-rank 4
  uv run python scripts/upside_detection.py --start 20250101 --end 20261231 --report docs/upside/
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import engine
from src.indices.composite import COMPOSITE_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("upside_detection")

# ---------------------------------------------------------------------------
# 個別指数カラム（総合指数 / 勝率 は別途扱う）
# ---------------------------------------------------------------------------
INDEX_COLS = [
    "speed_index",
    "last_3f_index",
    "course_aptitude",
    "position_advantage",
    "jockey_index",
    "pace_index",
    "rotation_index",
    "pedigree_index",
    "training_index",
    "anagusa_index",
    "paddock_index",
]
INDEX_LABELS = {
    "speed_index": "スピード",
    "last_3f_index": "後3F",
    "course_aptitude": "コース適性",
    "position_advantage": "枠順",
    "jockey_index": "騎手",
    "pace_index": "展開",
    "rotation_index": "ローテ",
    "pedigree_index": "血統",
    "training_index": "調教",
    "anagusa_index": "穴ぐさ",
    "paddock_index": "パドック",
}


# ============================================================
# データ取得
# ============================================================


def load_data(start_date: str, end_date: str, version: int = COMPOSITE_VERSION) -> pd.DataFrame:
    """算出指数・レース結果・オッズを結合取得する。

    複勝オッズは odds_history から最終確定値（最小オッズ=低配当）を使用。
    """
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

    sql = text(f"""
WITH anagusa_picks AS (
    -- sekito.anagusa から実際のA/B/Cランクピックを取得
    SELECT
        sa.date::text AS race_date,
        sa.course_code,
        sa.race_no,
        sa.horse_no,
        sa.rank AS anagusa_actual_rank
    FROM sekito.anagusa sa
),
jra_sekito_map AS (
    SELECT * FROM (VALUES
        ('01','JSPK'), ('02','JHKD'), ('03','JFKS'), ('04','JNGT'), ('05','JTOK'),
        ('06','JNKY'), ('07','JCKO'), ('08','JKYO'), ('09','JHSN'), ('10','JKKR')
    ) AS t(jra_code, sekito_code)
),
place_odds AS (
    -- odds_history の複勝オッズ: 各馬番で最後に記録された値を確定オッズとみなす
    SELECT
        race_id,
        combination::int AS horse_number,
        odds AS place_odds
    FROM (
        SELECT race_id, combination, odds,
               ROW_NUMBER() OVER (PARTITION BY race_id, combination ORDER BY fetched_at DESC) AS rn
        FROM keiba.odds_history
        WHERE bet_type = 'place'
          AND combination ~ '^[0-9]+$'
    ) t
    WHERE rn = 1
)
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course          AS course,
    r.course_name     AS course_name,
    r.grade           AS grade,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    r.condition       AS condition,
    r.race_type_code  AS race_type_code,
    ci.horse_id       AS horse_id,
    ci.composite_index    AS composite_index,
    ci.speed_index        AS speed_index,
    ci.last_3f_index      AS last_3f_index,
    ci.course_aptitude    AS course_aptitude,
    ci.position_advantage AS position_advantage,
    ci.jockey_index       AS jockey_index,
    ci.pace_index         AS pace_index,
    ci.rotation_index     AS rotation_index,
    ci.pedigree_index     AS pedigree_index,
    ci.training_index     AS training_index,
    ci.anagusa_index      AS anagusa_index,
    ci.paddock_index      AS paddock_index,
    ci.win_probability    AS win_probability,
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds,
    rr.win_popularity     AS win_popularity,
    rr.horse_number       AS horse_number,
    po.place_odds         AS place_odds,
    ap.anagusa_actual_rank AS anagusa_actual_rank
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
LEFT JOIN place_odds po ON po.race_id = ci.race_id AND po.horse_number = rr.horse_number
LEFT JOIN jra_sekito_map jsm ON jsm.jra_code = r.course
LEFT JOIN anagusa_picks ap ON ap.race_date = TO_CHAR(TO_DATE(r.date, 'YYYYMMDD'), 'YYYY-MM-DD')
    AND ap.course_code = jsm.sekito_code
    AND ap.race_no = r.race_number
    AND ap.horse_no = rr.horse_number
WHERE r.date BETWEEN :start_date AND :end_date
  AND ci.version = {version}
ORDER BY r.date, r.id, ci.horse_id
""")

    with Session(engine) as db:
        result = db.execute(sql, {"start_date": sd, "end_date": ed})
        rows = result.fetchall()
        columns = list(result.keys())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=columns)

    # 型変換
    numeric_cols = [
        "composite_index",
        "win_probability",
        "win_odds",
        "win_popularity",
        "place_odds",
        "finish_position",
        "head_count",
        "distance",
        "abnormality_code",
        "horse_number",
    ] + INDEX_COLS
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    logger.info(f"取得: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


def filter_valid(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """異常・未算出・少頭数レースを除外する。"""
    bad = df[
        (df["abnormality_code"] > 0) | df["finish_position"].isna() | df["composite_index"].isna()
    ]["race_id"].unique()

    df = df[~df["race_id"].isin(bad)].copy()
    counts = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(counts[counts >= min_runners].index)].copy()

    logger.info(f"フィルタ後: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


# ============================================================
# 指数ランク付け・乖離スコア算出
# ============================================================


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """レース内での指数ランク・着順ランクを付与する。

    index_rank:  composite_index の降順ランク (1=指数最高)
    gap_score:   index_rank - finish_position  (正の値 = 穴馬的中)
    upside_flag: 指数ランクが4位以降かつ3着以内
    """
    df = df.copy()

    def _rank_within_race(group: pd.DataFrame) -> pd.Series:
        return group["composite_index"].rank(ascending=False, method="min")

    df["index_rank"] = df.groupby("race_id", group_keys=False).apply(_rank_within_race)

    # 指数順位の相対化（頭数に対する割合）
    df["index_rank_ratio"] = (df["index_rank"] - 1) / (df["head_count"] - 1).clip(lower=1)

    # 乖離スコア: 高いほど「指数低いのに着順良い」
    df["gap_score"] = df["index_rank"] - df["finish_position"]

    # 穴馬フラグ: 指数4位以降 かつ 3着以内
    df["upside_flag"] = (df["index_rank"] >= 4) & (df["finish_position"] <= 3)

    # 大穴フラグ: 指数4位以降 かつ 1着
    df["big_upset_flag"] = (df["index_rank"] >= 4) & (df["finish_position"] == 1)

    # 人気穴フラグ: 指数は上位でも人気が低い（市場の見逃し）
    df["odds_gap"] = df.get("win_popularity", np.nan) - df["index_rank"]

    return df


def add_individual_index_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """各個別指数のレース内ランクを追加する。"""
    df = df.copy()
    for col in INDEX_COLS:
        if col not in df.columns:
            continue
        rank_col = f"{col}_rank"
        df[rank_col] = df.groupby("race_id")[col].rank(ascending=False, method="min")
    return df


# ============================================================
# 穴馬パターン分析
# ============================================================


def analyze_upside_patterns(df: pd.DataFrame) -> dict:
    """指数下位で馬券になった馬の特徴パターンを抽出する。

    Returns:
        dict: 各分析結果の DataFrame を格納した辞書
    """
    results = {}

    upside = df[df["upside_flag"]].copy()
    control = df[~df["upside_flag"] & (df["finish_position"] > 3)].copy()

    logger.info(
        f"穴馬ケース: {len(upside):,} 件 / 全指数下位馬: {len(df[df['index_rank'] >= 4]):,} 件"
    )

    # ------------------------------------------------------------------
    # 1. 個別指数突出分析
    # ------------------------------------------------------------------
    # 穴馬的中時に各個別指数が「その馬のindex_rank以内に何位か」を計算
    # 個別指数ランクが総合よりも上位の場合に「突出」とみなす
    rank_cols = [f"{c}_rank" for c in INDEX_COLS if f"{c}_rank" in df.columns]

    if rank_cols:
        prominence_data = []
        for col in INDEX_COLS:
            rank_col = f"{col}_rank"
            if rank_col not in upside.columns:
                continue
            # 総合指数ランクよりも上位（数値が小さい）かどうか
            upside_prominent = (upside[rank_col] < upside["index_rank"]).mean()
            control_prominent = (
                (control[rank_col] < control["index_rank"]).mean() if len(control) > 0 else 0
            )
            lift = upside_prominent - control_prominent
            prominence_data.append(
                {
                    "指数名": INDEX_LABELS.get(col, col),
                    "col": col,
                    "穴馬的中時_突出率": round(upside_prominent, 4),
                    "外れ時_突出率": round(control_prominent, 4),
                    "リフト": round(lift, 4),
                    "穴馬的中時_平均ランク": round(upside[rank_col].mean(), 2),
                    "外れ時_平均ランク": round(control[rank_col].mean(), 2),
                }
            )
        results["individual_prominence"] = pd.DataFrame(prominence_data).sort_values(
            "リフト", ascending=False
        )

    # ------------------------------------------------------------------
    # 2. 条件別穴率（指数下位で馬券になる確率）
    # ------------------------------------------------------------------
    low_rank_df = df[df["index_rank"] >= 4].copy()

    # 馬場別
    if "surface" in df.columns:
        surface_stats = (
            low_rank_df.groupby("surface")
            .agg(
                total=("upside_flag", "count"),
                upside=("upside_flag", "sum"),
            )
            .assign(upside_rate=lambda x: x["upside"] / x["total"])
            .reset_index()
            .sort_values("upside_rate", ascending=False)
        )
        results["by_surface"] = surface_stats

    # グレード別
    if "grade" in df.columns:
        grade_stats = (
            low_rank_df.groupby("grade")
            .agg(total=("upside_flag", "count"), upside=("upside_flag", "sum"))
            .assign(upside_rate=lambda x: x["upside"] / x["total"])
            .reset_index()
            .sort_values("upside_rate", ascending=False)
        )
        results["by_grade"] = grade_stats

    # 馬場状態別
    if "condition" in df.columns:
        cond_stats = (
            low_rank_df.groupby("condition")
            .agg(total=("upside_flag", "count"), upside=("upside_flag", "sum"))
            .assign(upside_rate=lambda x: x["upside"] / x["total"])
            .reset_index()
            .sort_values("upside_rate", ascending=False)
        )
        results["by_condition"] = cond_stats

    # 距離帯別（1200m以下 / 1400-1800 / 2000-2400 / 2500以上）
    if "distance" in df.columns:
        bins = [0, 1200, 1800, 2400, 9999]
        labels = ["短距離(～1200)", "マイル(1400-1800)", "中距離(2000-2400)", "長距離(2500+)"]
        low_rank_df = low_rank_df.copy()
        low_rank_df["dist_band"] = pd.cut(low_rank_df["distance"], bins=bins, labels=labels)
        dist_stats = (
            low_rank_df.groupby("dist_band", observed=True)
            .agg(total=("upside_flag", "count"), upside=("upside_flag", "sum"))
            .assign(upside_rate=lambda x: x["upside"] / x["total"])
            .reset_index()
            .sort_values("upside_rate", ascending=False)
        )
        results["by_distance"] = dist_stats

    # ------------------------------------------------------------------
    # 3. オッズ乖離分析（指数低いが市場も人気がない = 本物の穴馬）
    # ------------------------------------------------------------------
    if "win_odds" in df.columns and "win_popularity" in df.columns:
        # 指数下位 かつ 人気下位 かつ 3着以内
        double_low = df[
            (df["index_rank"] >= 4) & (df["win_popularity"] >= 4) & (df["finish_position"] <= 3)
        ]
        results["double_low_summary"] = {
            "count": len(double_low),
            "avg_win_odds": round(double_low["win_odds"].mean(), 1),
            "median_win_odds": round(double_low["win_odds"].median(), 1),
            "avg_index_rank": round(double_low["index_rank"].mean(), 1),
            "avg_popularity": round(double_low["win_popularity"].mean(), 1),
        }

        # 単勝回収率（指数下位馬を一律購入した場合）
        low_rank_all = df[df["index_rank"] >= 4].copy()
        total_bet = len(low_rank_all)
        wins = low_rank_all[low_rank_all["finish_position"] == 1]
        total_return = wins["win_odds"].sum() * 100  # 100円単位
        roi = total_return / (total_bet * 100) if total_bet > 0 else 0
        results["low_rank_roi"] = {
            "total_bets": total_bet,
            "wins": len(wins),
            "win_rate": round(len(wins) / total_bet, 4) if total_bet > 0 else 0,
            "roi": round(roi, 4),
        }

    # ------------------------------------------------------------------
    # 4. 穴馬的中時の指数プロファイル（各指数の平均値 比較）
    # ------------------------------------------------------------------
    profile_rows = []
    for col in ["composite_index"] + INDEX_COLS:
        if col not in df.columns:
            continue
        val_up = upside[col].dropna().mean()
        val_ctrl = control[col].dropna().mean()
        all_mean = df[col].dropna().mean()
        profile_rows.append(
            {
                "指数名": INDEX_LABELS.get(col, col) if col != "composite_index" else "総合",
                "col": col,
                "穴馬的中_平均": round(val_up, 2),
                "外れ(下位馬)_平均": round(val_ctrl, 2),
                "全体_平均": round(all_mean, 2),
                "差(穴-外れ)": round(val_up - val_ctrl, 2),
            }
        )
    results["index_profile"] = pd.DataFrame(profile_rows).sort_values(
        "差(穴-外れ)", ascending=False
    )

    return results


# ============================================================
# 穴馬スコア (UpsideScore) の設計・試算
# ============================================================


def compute_upside_score(df: pd.DataFrame) -> pd.DataFrame:
    """穴馬スコアを算出する（試験的実装）。

    考え方:
      - 総合指数は低くても、特定の個別指数が突出している馬に高いスコアを与える
      - 突出度 = (その馬の個別指数ランク - 総合指数ランク) → 正の値ほど突出
      - 穴ぐさ・パドック指数は現在の重みが0なので、穴馬スコアでは考慮する

    スコア構成（暫定）:
      upside_score = Σ max(0, index_rank - individual_rank) × weight

    Returns:
        df with "upside_score" column added
    """
    # 穴馬スコアに使う個別指数と重み（実測リフト比例）
    # 根拠: 2024-2026実績分析（upside_detection.py 第1回実行結果）
    #   穴ぐさ     +6.3% → 0.30
    #   コース適性 +5.9% → 0.28
    #   パドック   +5.0% → 0.23
    #   血統       +3.4% → 0.10
    #   騎手       +2.9% → 0.09
    #   ローテ・調教・展開・枠順 は負のリフト → 除外
    UPSIDE_WEIGHTS = {
        "anagusa_index": 0.30,  # 穴ぐさ指数 → 市場外シグナル（最強リフト）
        "course_aptitude": 0.28,  # コース適性 → コース巧者
        "paddock_index": 0.23,  # パドック → 当日状態
        "pedigree_index": 0.10,  # 血統 → 条件適性
        "jockey_index": 0.09,  # 騎手 → 乗り替わり等
    }

    df = df.copy()

    # 各個別指数のレース内ランク
    for col in UPSIDE_WEIGHTS:
        rank_col = f"{col}_rank"
        if rank_col not in df.columns:
            if col in df.columns:
                df[rank_col] = df.groupby("race_id")[col].rank(ascending=False, method="min")

    # 突出スコア算出
    prominence_score = pd.Series(0.0, index=df.index)
    for col, w in UPSIDE_WEIGHTS.items():
        rank_col = f"{col}_rank"
        if rank_col not in df.columns:
            continue
        # 個別指数がレース内でindex_rankよりも順位が高い（数値が小さい）ほど加点
        prominence = (df["index_rank"] - df[rank_col]).clip(lower=0)
        # 最大値で正規化
        max_val = prominence.max()
        if max_val > 0:
            prominence = prominence / max_val
        prominence_score += prominence * w

    # 人気とのギャップ加点（市場が見逃している = 人気が低い）
    if "win_popularity" in df.columns and "index_rank" in df.columns:
        # 人気が高い（数値小）のに指数が高い = 市場と一致 → 加点しない
        # 人気が低い（数値大）のに指数では下位 = 市場も評価低い
        # 穴馬スコアでは「指数は低いが人気も低い（市場評価と一致）で当たった場合を特徴化」
        pop_gap = (df["win_popularity"] - df["index_rank"]).clip(lower=0)
        max_pop = pop_gap.max()
        if max_pop > 0:
            pop_gap = pop_gap / max_pop
        prominence_score += pop_gap * 0.1  # 人気ギャップは補助的

    df["upside_score"] = prominence_score.round(4)
    return df


# ============================================================
# UpsideScore 有効性検証
# ============================================================


def validate_upside_score(df: pd.DataFrame) -> dict:
    """UpsideScore が穴馬的中を予測できるか検証する。

    Returns:
        dict: 閾値別の的中率・回収率
    """
    low_rank_df = df[df["index_rank"] >= 4].copy()
    if low_rank_df.empty:
        return {}

    results = {}

    # スピアマン相関（upside_score vs finish_position） - scipy不使用で実装
    valid = low_rank_df.dropna(subset=["upside_score", "finish_position"])
    if len(valid) >= 10:
        x = (-valid["upside_score"]).rank()
        y = valid["finish_position"].rank()
        n = len(x)
        rho = 1 - 6 * ((x - y) ** 2).sum() / (n * (n**2 - 1))
        # p値の近似（t分布）
        import math

        t_stat = rho * math.sqrt((n - 2) / max(1 - rho**2, 1e-10))
        # 正規近似
        pval = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
        results["spearman_correlation"] = {
            "rho": round(float(rho), 4),
            "pval": round(float(pval), 6),
        }

    # 閾値別の3着以内率・単勝回収率
    threshold_rows = []
    for thresh in [0.3, 0.4, 0.5, 0.6, 0.7]:
        subset = low_rank_df[low_rank_df["upside_score"] >= thresh]
        if len(subset) < 5:
            continue
        place_rate = (subset["finish_position"] <= 3).mean()
        win_rate = (subset["finish_position"] == 1).mean()
        avg_odds = subset["win_odds"].mean() if "win_odds" in subset.columns else np.nan
        roi = (
            (subset[subset["finish_position"] == 1]["win_odds"].sum() * 100 / (len(subset) * 100))
            if len(subset) > 0
            else 0
        )
        threshold_rows.append(
            {
                "upside_score閾値": thresh,
                "対象馬数": len(subset),
                "3着内率": round(place_rate, 4),
                "単勝率": round(win_rate, 4),
                "平均単勝オッズ": round(avg_odds, 1) if not np.isnan(avg_odds) else "-",
                "単勝ROI": round(roi, 4),
            }
        )
    results["threshold_validation"] = pd.DataFrame(threshold_rows)

    # 上位スコアのみ: UpsideScore ランク上位N頭に絞った場合
    score_rank_rows = []
    for top_n in [1, 2, 3]:
        # レース内でupside_scoreが高い上位N頭
        df_top = low_rank_df.copy()
        df_top["upside_rank"] = df_top.groupby("race_id")["upside_score"].rank(
            ascending=False, method="min"
        )
        subset = df_top[df_top["upside_rank"] <= top_n]
        if len(subset) < 5:
            continue
        place_rate = (subset["finish_position"] <= 3).mean()
        win_rate = (subset["finish_position"] == 1).mean()
        roi = (
            (subset[subset["finish_position"] == 1]["win_odds"].sum() * 100 / (len(subset) * 100))
            if len(subset) > 0
            else 0
        )
        score_rank_rows.append(
            {
                "指数下位上位N頭(upside)": top_n,
                "対象馬数": len(subset),
                "3着内率": round(place_rate, 4),
                "単勝率": round(win_rate, 4),
                "単勝ROI": round(roi, 4),
            }
        )
    results["top_n_validation"] = pd.DataFrame(score_rank_rows)

    # ------------------------------------------------------------------
    # 複合条件フィルタ検証
    # ------------------------------------------------------------------
    # 条件1: コース適性突出 かつ 穴ぐさ指数突出
    combo_rows = []
    if all(c in low_rank_df.columns for c in ["course_aptitude_rank", "anagusa_index_rank"]):
        ca_prominent = low_rank_df["index_rank"] > low_rank_df["course_aptitude_rank"]
        ana_prominent = low_rank_df["index_rank"] > low_rank_df["anagusa_index_rank"]
        combo1 = low_rank_df[ca_prominent & ana_prominent]
        if len(combo1) >= 5:
            roi1 = (
                (
                    combo1[combo1["finish_position"] == 1]["win_odds"].sum()
                    * 100
                    / (len(combo1) * 100)
                )
                if len(combo1) > 0
                else 0
            )
            combo_rows.append(
                {
                    "フィルタ条件": "コース適性↑ × 穴ぐさ↑",
                    "対象馬数": len(combo1),
                    "3着内率": round((combo1["finish_position"] <= 3).mean(), 4),
                    "単勝率": round((combo1["finish_position"] == 1).mean(), 4),
                    "単勝ROI": round(roi1, 4),
                }
            )

    # 条件2: 悪化馬場（重・不良）× コース適性突出
    if "condition" in low_rank_df.columns and "course_aptitude_rank" in low_rank_df.columns:
        bad_track = low_rank_df["condition"].isin(["重", "不"])
        ca_prom = low_rank_df["index_rank"] > low_rank_df["course_aptitude_rank"]
        combo2 = low_rank_df[bad_track & ca_prom]
        if len(combo2) >= 5:
            roi2 = (
                (
                    combo2[combo2["finish_position"] == 1]["win_odds"].sum()
                    * 100
                    / (len(combo2) * 100)
                )
                if len(combo2) > 0
                else 0
            )
            combo_rows.append(
                {
                    "フィルタ条件": "悪化馬場(重/不) × コース適性↑",
                    "対象馬数": len(combo2),
                    "3着内率": round((combo2["finish_position"] <= 3).mean(), 4),
                    "単勝率": round((combo2["finish_position"] == 1).mean(), 4),
                    "単勝ROI": round(roi2, 4),
                }
            )

    # 条件3: 穴ぐさ突出 × パドック突出
    if all(c in low_rank_df.columns for c in ["anagusa_index_rank", "paddock_index_rank"]):
        ana_prom = low_rank_df["index_rank"] > low_rank_df["anagusa_index_rank"]
        pad_prom = low_rank_df["index_rank"] > low_rank_df["paddock_index_rank"]
        combo3 = low_rank_df[ana_prom & pad_prom]
        if len(combo3) >= 5:
            roi3 = (
                (
                    combo3[combo3["finish_position"] == 1]["win_odds"].sum()
                    * 100
                    / (len(combo3) * 100)
                )
                if len(combo3) > 0
                else 0
            )
            combo_rows.append(
                {
                    "フィルタ条件": "穴ぐさ↑ × パドック↑",
                    "対象馬数": len(combo3),
                    "3着内率": round((combo3["finish_position"] <= 3).mean(), 4),
                    "単勝率": round((combo3["finish_position"] == 1).mean(), 4),
                    "単勝ROI": round(roi3, 4),
                }
            )

    # 条件4: コース適性 × 穴ぐさ × パドック（3条件複合）
    if all(
        c in low_rank_df.columns
        for c in ["course_aptitude_rank", "anagusa_index_rank", "paddock_index_rank"]
    ):
        combo4 = low_rank_df[
            (low_rank_df["index_rank"] > low_rank_df["course_aptitude_rank"])
            & (low_rank_df["index_rank"] > low_rank_df["anagusa_index_rank"])
            & (low_rank_df["index_rank"] > low_rank_df["paddock_index_rank"])
        ]
        if len(combo4) >= 5:
            roi4 = (
                (
                    combo4[combo4["finish_position"] == 1]["win_odds"].sum()
                    * 100
                    / (len(combo4) * 100)
                )
                if len(combo4) > 0
                else 0
            )
            combo_rows.append(
                {
                    "フィルタ条件": "コース適性↑ × 穴ぐさ↑ × パドック↑",
                    "対象馬数": len(combo4),
                    "3着内率": round((combo4["finish_position"] <= 3).mean(), 4),
                    "単勝率": round((combo4["finish_position"] == 1).mean(), 4),
                    "単勝ROI": round(roi4, 4),
                }
            )

    if combo_rows:
        results["combo_filter_validation"] = pd.DataFrame(combo_rows)

    # ------------------------------------------------------------------
    # 指数ランク帯別の分析（4-6 / 7-10 / 11+）
    # ------------------------------------------------------------------
    band_rows = []
    for label, lo, hi in [("4-6番手", 4, 6), ("7-10番手", 7, 10), ("11番手以降", 11, 999)]:
        band = low_rank_df[(low_rank_df["index_rank"] >= lo) & (low_rank_df["index_rank"] <= hi)]
        if len(band) < 5:
            continue
        roi_b = (
            (band[band["finish_position"] == 1]["win_odds"].sum() * 100 / (len(band) * 100))
            if len(band) > 0
            else 0
        )
        avg_odds = band["win_odds"].mean() if "win_odds" in band.columns else np.nan
        band_rows.append(
            {
                "指数ランク帯": label,
                "対象馬数": len(band),
                "3着内率": round((band["finish_position"] <= 3).mean(), 4),
                "単勝率": round((band["finish_position"] == 1).mean(), 4),
                "平均単勝オッズ": round(avg_odds, 1) if not np.isnan(avg_odds) else "-",
                "単勝ROI": round(roi_b, 4),
            }
        )

        # UpsideScore上位1頭に絞った場合
        top1 = band.copy()
        top1["upside_rank_b"] = top1.groupby("race_id")["upside_score"].rank(
            ascending=False, method="min"
        )
        top1 = top1[top1["upside_rank_b"] == 1]
        if len(top1) >= 5:
            roi_t1 = (
                (top1[top1["finish_position"] == 1]["win_odds"].sum() * 100 / (len(top1) * 100))
                if len(top1) > 0
                else 0
            )
            avg_t1 = top1["win_odds"].mean() if "win_odds" in top1.columns else np.nan
            band_rows.append(
                {
                    "指数ランク帯": f"{label}（穴スコア1位）",
                    "対象馬数": len(top1),
                    "3着内率": round((top1["finish_position"] <= 3).mean(), 4),
                    "単勝率": round((top1["finish_position"] == 1).mean(), 4),
                    "平均単勝オッズ": round(avg_t1, 1) if not np.isnan(avg_t1) else "-",
                    "単勝ROI": round(roi_t1, 4),
                }
            )

    results["rank_band_validation"] = pd.DataFrame(band_rows)

    # ------------------------------------------------------------------
    # オッズ範囲 × 指数ランク帯の交差分析
    # 「指数は下位だが市場が過小評価（オッズが相対的に低め）」を探す
    # ------------------------------------------------------------------
    if "win_odds" not in low_rank_df.columns:
        return results

    odds_band_rows = []
    for idx_label, idx_lo, idx_hi in [("4-6番手", 4, 6), ("7-10番手", 7, 10)]:
        band = low_rank_df[
            (low_rank_df["index_rank"] >= idx_lo) & (low_rank_df["index_rank"] <= idx_hi)
        ]
        if band.empty:
            continue
        for odds_label, odds_lo, odds_hi in [
            ("低オッズ(3-9x)", 3.0, 9.9),
            ("中オッズ(10-29x)", 10.0, 29.9),
            ("高オッズ(30-99x)", 30.0, 99.9),
        ]:
            subset = band[(band["win_odds"] >= odds_lo) & (band["win_odds"] < odds_hi)]
            if len(subset) < 10:
                continue
            win_rate = (subset["finish_position"] == 1).mean()
            roi = (
                subset[subset["finish_position"] == 1]["win_odds"].sum() * 100 / (len(subset) * 100)
            )
            # 穴スコア1位に絞った場合
            top1 = subset.copy()
            top1["usrank"] = top1.groupby("race_id")["upside_score"].rank(
                ascending=False, method="min"
            )
            top1 = top1[top1["usrank"] == 1]
            roi_top1 = (
                (top1[top1["finish_position"] == 1]["win_odds"].sum() * 100 / (len(top1) * 100))
                if len(top1) >= 5
                else float("nan")
            )
            win_rate_top1 = (
                (top1["finish_position"] == 1).mean() if len(top1) >= 5 else float("nan")
            )

            odds_band_rows.append(
                {
                    "指数ランク帯": idx_label,
                    "オッズ帯": odds_label,
                    "対象馬数": len(subset),
                    "単勝率": round(win_rate, 4),
                    "単勝ROI": round(roi, 4),
                    "穴スコア1位_馬数": len(top1) if len(top1) >= 5 else "-",
                    "穴スコア1位_単勝率": round(win_rate_top1, 4)
                    if not np.isnan(win_rate_top1)
                    else "-",
                    "穴スコア1位_ROI": round(roi_top1, 4) if not np.isnan(roi_top1) else "-",
                }
            )

    results["odds_band_analysis"] = pd.DataFrame(odds_band_rows)

    # ------------------------------------------------------------------
    # 最有望パターンの深掘り
    # 4-6番手 × 中オッズ(10-29x) × 穴スコア1位 でさらに絞り込み
    # ------------------------------------------------------------------
    deep_rows = []
    base = low_rank_df[
        (low_rank_df["index_rank"] >= 4)
        & (low_rank_df["index_rank"] <= 6)
        & (low_rank_df["win_odds"] >= 10.0)
        & (low_rank_df["win_odds"] < 30.0)
    ].copy()
    base["usrank"] = base.groupby("race_id")["upside_score"].rank(ascending=False, method="min")
    base_top1 = base[base["usrank"] == 1]

    # 悪化馬場フィルタ
    if "condition" in base_top1.columns:
        for cond_label, cond_vals in [
            ("全馬場", None),
            ("稍重以上(稍/重/不)", ["稍", "重", "不"]),
            ("重・不良", ["重", "不"]),
        ]:
            if cond_vals:
                sub = base_top1[base_top1["condition"].isin(cond_vals)]
            else:
                sub = base_top1
            if len(sub) < 5:
                continue
            roi_v = sub[sub["finish_position"] == 1]["win_odds"].sum() * 100 / (len(sub) * 100)
            deep_rows.append(
                {
                    "絞り込み条件": f"4-6番手×中オッズ×穴スコア1位 [{cond_label}]",
                    "対象馬数": len(sub),
                    "単勝率": round((sub["finish_position"] == 1).mean(), 4),
                    "3着内率": round((sub["finish_position"] <= 3).mean(), 4),
                    "単勝ROI": round(roi_v, 4),
                }
            )

    # コース適性突出フィルタ
    if "course_aptitude_rank" in base_top1.columns:
        ca_prom = base_top1[base_top1["index_rank"] > base_top1["course_aptitude_rank"]]
        if len(ca_prom) >= 5:
            roi_ca = (
                ca_prom[ca_prom["finish_position"] == 1]["win_odds"].sum()
                * 100
                / (len(ca_prom) * 100)
            )
            deep_rows.append(
                {
                    "絞り込み条件": "4-6番手×中オッズ×穴スコア1位 [コース適性突出]",
                    "対象馬数": len(ca_prom),
                    "単勝率": round((ca_prom["finish_position"] == 1).mean(), 4),
                    "3着内率": round((ca_prom["finish_position"] <= 3).mean(), 4),
                    "単勝ROI": round(roi_ca, 4),
                }
            )

    # 穴ぐさ突出フィルタ（指数ランクベース）
    if "anagusa_index_rank" in base_top1.columns:
        ana_prom = base_top1[base_top1["index_rank"] > base_top1["anagusa_index_rank"]]
        if len(ana_prom) >= 5:
            roi_ana = (
                ana_prom[ana_prom["finish_position"] == 1]["win_odds"].sum()
                * 100
                / (len(ana_prom) * 100)
            )
            deep_rows.append(
                {
                    "絞り込み条件": "4-6番手×中オッズ×穴スコア1位 [穴ぐさ突出]",
                    "対象馬数": len(ana_prom),
                    "単勝率": round((ana_prom["finish_position"] == 1).mean(), 4),
                    "3着内率": round((ana_prom["finish_position"] <= 3).mean(), 4),
                    "単勝ROI": round(roi_ana, 4),
                }
            )

    # 実際のanagusa A/Bランクピックフィルタ
    if "anagusa_actual_rank" in base_top1.columns:
        for rank_label, rank_vals in [("実Aランク", ["A"]), ("実A/Bランク", ["A", "B"])]:
            ana_actual = base_top1[base_top1["anagusa_actual_rank"].isin(rank_vals)]
            if len(ana_actual) >= 5:
                roi_aa = (
                    ana_actual[ana_actual["finish_position"] == 1]["win_odds"].sum()
                    * 100
                    / (len(ana_actual) * 100)
                )
                deep_rows.append(
                    {
                        "絞り込み条件": f"4-6番手×中オッズ×穴スコア1位 [穴ぐさ{rank_label}]",
                        "対象馬数": len(ana_actual),
                        "単勝率": round((ana_actual["finish_position"] == 1).mean(), 4),
                        "3着内率": round((ana_actual["finish_position"] <= 3).mean(), 4),
                        "単勝ROI": round(roi_aa, 4),
                    }
                )

    # コース適性 × 穴ぐさ 両突出
    if all(c in base_top1.columns for c in ["course_aptitude_rank", "anagusa_index_rank"]):
        both_prom = base_top1[
            (base_top1["index_rank"] > base_top1["course_aptitude_rank"])
            & (base_top1["index_rank"] > base_top1["anagusa_index_rank"])
        ]
        if len(both_prom) >= 5:
            roi_both = (
                both_prom[both_prom["finish_position"] == 1]["win_odds"].sum()
                * 100
                / (len(both_prom) * 100)
            )
            deep_rows.append(
                {
                    "絞り込み条件": "4-6番手×中オッズ×穴スコア1位 [コース適性↑×穴ぐさ↑]",
                    "対象馬数": len(both_prom),
                    "単勝率": round((both_prom["finish_position"] == 1).mean(), 4),
                    "3着内率": round((both_prom["finish_position"] <= 3).mean(), 4),
                    "単勝ROI": round(roi_both, 4),
                }
            )

    if deep_rows:
        results["deep_dive"] = pd.DataFrame(deep_rows)

    # ------------------------------------------------------------------
    # 複勝ROI分析（最有望パターン）
    # place_odds カラムがある場合のみ実行
    # ------------------------------------------------------------------
    if "place_odds" in low_rank_df.columns and low_rank_df["place_odds"].notna().sum() > 100:
        place_rows = []
        for label, subset in [
            ("4-6番手×穴スコア1位（全体）", base_top1),
            ("4-6番手×中オッズ×穴スコア1位", base_top1),
        ]:
            # 複勝収れい数: 頭数により2着または3着以内
            def _place_thresh(row):
                hc = row.get("head_count", 8)
                return 3 if (hc or 8) >= 8 else 2

            sub = subset.dropna(subset=["place_odds"]).copy()
            if len(sub) < 5:
                continue
            sub["in_place"] = sub.apply(
                lambda r: (
                    r["finish_position"] is not None and r["finish_position"] <= _place_thresh(r)
                ),
                axis=1,
            )
            place_return = sub[sub["in_place"]]["place_odds"].sum() * 100
            place_roi = place_return / (len(sub) * 100) if len(sub) > 0 else 0
            place_rows.append(
                {
                    "フィルタ": label,
                    "対象馬数": len(sub),
                    "複勝的中数": int(sub["in_place"].sum()),
                    "複勝率": round(sub["in_place"].mean(), 4),
                    "平均複勝オッズ": round(sub["place_odds"].mean(), 2),
                    "複勝ROI": round(place_roi, 4),
                }
            )
        if place_rows:
            results["place_roi"] = pd.DataFrame(place_rows)

    return results


# ============================================================
# レポート出力
# ============================================================


def print_report(patterns: dict, validation: dict, params: dict) -> str:
    """分析結果をMarkdown形式でフォーマットする。"""
    lines = []
    lines.append("# 指数下位馬券検出分析レポート")
    lines.append(f"\n**期間:** {params['start_date']} ～ {params['end_date']}")
    lines.append(f"**指数下位判定:** index_rank >= {params['min_index_rank']}")
    lines.append("")

    # 個別指数突出パターン
    if "individual_prominence" in patterns:
        lines.append("## 1. 個別指数突出パターン（穴馬的中時 vs 外れ時）")
        lines.append("指数下位なのに的中した馬が、どの個別指数が総合ランクより高かったか")
        lines.append("")
        lines.append(
            "| 指数名 | 穴馬的中_突出率 | 外れ時_突出率 | リフト | 穴馬的中_平均ランク | 外れ時_平均ランク |"
        )
        lines.append(
            "|--------|---------------|-------------|--------|-------------------|-----------------|"
        )
        for _, row in patterns["individual_prominence"].iterrows():
            lines.append(
                f"| {row['指数名']} | {row['穴馬的中時_突出率']:.1%} | {row['外れ時_突出率']:.1%} | "
                f"+{row['リフト']:.1%} | {row['穴馬的中時_平均ランク']} | {row['外れ時_平均ランク']} |"
            )
        lines.append("")

    # 指数プロファイル
    if "index_profile" in patterns:
        lines.append("## 2. 指数プロファイル比較（穴馬的中 vs 外れ）")
        lines.append("")
        lines.append("| 指数名 | 穴馬的中_平均 | 外れ(下位)_平均 | 差 |")
        lines.append("|--------|-------------|----------------|-----|")
        for _, row in patterns["index_profile"].iterrows():
            mark = "◎" if row["差(穴-外れ)"] > 1.0 else ("○" if row["差(穴-外れ)"] > 0.3 else "")
            lines.append(
                f"| {row['指数名']} {mark} | {row['穴馬的中_平均']} | {row['外れ(下位馬)_平均']} | {row['差(穴-外れ)']:+.2f} |"
            )
        lines.append("")

    # 条件別穴率
    for key, label in [
        ("by_surface", "馬場別"),
        ("by_condition", "馬場状態別"),
        ("by_grade", "グレード別"),
        ("by_distance", "距離帯別"),
    ]:
        if key in patterns:
            lines.append(f"## 3-{label} 穴率（指数下位で3着内率）")
            df_cond = patterns[key]
            col0 = df_cond.columns[0]
            lines.append(f"\n| {col0} | 総馬数 | 穴馬的中 | 穴率 |")
            lines.append("|------|-------|---------|------|")
            for _, row in df_cond.iterrows():
                lines.append(
                    f"| {row[col0]} | {int(row['total'])} | {int(row['upside'])} | {row['upside_rate']:.1%} |"
                )
            lines.append("")

    # 大穴サマリー
    if "double_low_summary" in patterns:
        s = patterns["double_low_summary"]
        lines.append("## 4. 大穴サマリー（指数下位 × 人気下位 で3着内）")
        lines.append(f"- 件数: {s['count']:,}")
        lines.append(f"- 平均単勝オッズ: {s['avg_win_odds']}")
        lines.append(f"- 中央単勝オッズ: {s['median_win_odds']}")
        lines.append(f"- 平均指数ランク: {s['avg_index_rank']}")
        lines.append(f"- 平均人気順位: {s['avg_popularity']}")
        lines.append("")

    if "low_rank_roi" in patterns:
        r = patterns["low_rank_roi"]
        lines.append("## 5. 指数下位馬を全購入した場合（参考）")
        lines.append(f"- 総買い目: {r['total_bets']:,}")
        lines.append(f"- 的中数: {r['wins']:,}")
        lines.append(f"- 的中率: {r['win_rate']:.1%}")
        lines.append(f"- 単勝ROI: {r['roi']:.1%}")
        lines.append("")

    # UpsideScore 検証
    if "spearman_correlation" in validation:
        sc = validation["spearman_correlation"]
        lines.append("## 6. UpsideScore 有効性検証")
        lines.append(
            f"スピアマン相関（-upside_score vs finish_position）: ρ={sc['rho']}, p={sc['pval']}"
        )
        lines.append("")

    if "threshold_validation" in validation and not validation["threshold_validation"].empty:
        lines.append("### 閾値別の的中率・回収率")
        df_thresh = validation["threshold_validation"]
        lines.append("\n| 閾値 | 対象馬数 | 3着内率 | 単勝率 | 平均オッズ | 単勝ROI |")
        lines.append("|------|---------|--------|--------|----------|---------|")
        for _, row in df_thresh.iterrows():
            lines.append(
                f"| {row['upside_score閾値']} | {int(row['対象馬数'])} | {row['3着内率']:.1%} | "
                f"{row['単勝率']:.1%} | {row['平均単勝オッズ']} | {row['単勝ROI']:.1%} |"
            )
        lines.append("")

    if "top_n_validation" in validation and not validation["top_n_validation"].empty:
        lines.append("### UpsideScore上位N頭の的中率・回収率")
        df_topn = validation["top_n_validation"]
        lines.append("\n| 上位N頭 | 対象馬数 | 3着内率 | 単勝率 | 単勝ROI |")
        lines.append("|--------|---------|--------|--------|---------|")
        for _, row in df_topn.iterrows():
            lines.append(
                f"| {int(row['指数下位上位N頭(upside)'])} | {int(row['対象馬数'])} | "
                f"{row['3着内率']:.1%} | {row['単勝率']:.1%} | {row['単勝ROI']:.1%} |"
            )
        lines.append("")

    if "combo_filter_validation" in validation and not validation["combo_filter_validation"].empty:
        lines.append("## 7. 複合条件フィルタ検証（指数下位 × 複数シグナル）")
        df_combo = validation["combo_filter_validation"]
        lines.append("\n| フィルタ条件 | 対象馬数 | 3着内率 | 単勝率 | 単勝ROI |")
        lines.append("|------------|---------|--------|--------|---------|")
        for _, row in df_combo.iterrows():
            roi_mark = " ★" if row["単勝ROI"] >= 0.80 else ""
            lines.append(
                f"| {row['フィルタ条件']} | {int(row['対象馬数'])} | "
                f"{row['3着内率']:.1%} | {row['単勝率']:.1%} | {row['単勝ROI']:.1%}{roi_mark} |"
            )
        lines.append("")

    if "rank_band_validation" in validation and not validation["rank_band_validation"].empty:
        lines.append("## 8. 指数ランク帯別分析（4-6 / 7-10 / 11+）")
        df_band = validation["rank_band_validation"]
        lines.append("\n| 指数ランク帯 | 対象馬数 | 3着内率 | 単勝率 | 平均オッズ | 単勝ROI |")
        lines.append("|------------|---------|--------|--------|----------|---------|")
        for _, row in df_band.iterrows():
            roi_mark = " ★" if row["単勝ROI"] >= 0.80 else ""
            lines.append(
                f"| {row['指数ランク帯']} | {int(row['対象馬数'])} | "
                f"{row['3着内率']:.1%} | {row['単勝率']:.1%} | {row['平均単勝オッズ']} | {row['単勝ROI']:.1%}{roi_mark} |"
            )
        lines.append("")

    if "odds_band_analysis" in validation and not validation["odds_band_analysis"].empty:
        lines.append("## 9. オッズ帯 × 指数ランク帯 交差分析")
        lines.append("（「市場が中程度に過小評価している穴馬」の探索）")
        df_odds = validation["odds_band_analysis"]
        lines.append(
            "\n| 指数ランク帯 | オッズ帯 | 対象馬数 | 単勝率 | 単勝ROI | 穴スコア1位_馬数 | 穴スコア1位_単勝率 | 穴スコア1位_ROI |"
        )
        lines.append(
            "|------------|--------|---------|--------|---------|---------------|-----------------|--------------|"
        )
        for _, row in df_odds.iterrows():
            roi_v = row["単勝ROI"]
            roi_t = row["穴スコア1位_ROI"]
            roi_mark = (
                " ★"
                if isinstance(roi_t, float) and roi_t >= 0.80
                else (" ◎" if isinstance(roi_v, float) and roi_v >= 0.80 else "")
            )
            n_top = (
                int(row["穴スコア1位_馬数"])
                if isinstance(row["穴スコア1位_馬数"], (int, float))
                else row["穴スコア1位_馬数"]
            )
            wr_t = (
                f"{row['穴スコア1位_単勝率']:.1%}"
                if isinstance(row["穴スコア1位_単勝率"], float)
                else "-"
            )
            roi_t_str = (
                f"{row['穴スコア1位_ROI']:.1%}"
                if isinstance(row["穴スコア1位_ROI"], float)
                else "-"
            )
            lines.append(
                f"| {row['指数ランク帯']} | {row['オッズ帯']} | {int(row['対象馬数'])} | "
                f"{row['単勝率']:.1%} | {roi_v:.1%} | {n_top} | {wr_t} | {roi_t_str}{roi_mark} |"
            )
        lines.append("")

    if "place_roi" in validation and not validation["place_roi"].empty:
        lines.append("## 10b. 複勝ROI分析（最有望パターン）")
        df_place = validation["place_roi"]
        lines.append("\n| フィルタ | 対象馬数 | 複勝率 | 平均複勝オッズ | 複勝ROI |")
        lines.append("|--------|---------|--------|-------------|---------|")
        for _, row in df_place.iterrows():
            roi_mark = " ★" if row["複勝ROI"] >= 1.00 else (" ◎" if row["複勝ROI"] >= 0.90 else "")
            lines.append(
                f"| {row['フィルタ']} | {int(row['対象馬数'])} | "
                f"{row['複勝率']:.1%} | {row['平均複勝オッズ']} | {row['複勝ROI']:.1%}{roi_mark} |"
            )
        lines.append("")

    if "deep_dive" in validation and not validation["deep_dive"].empty:
        lines.append("## 10. 最有望パターン深掘り（4-6番手×中オッズ×穴スコア1位）")
        df_deep = validation["deep_dive"]
        lines.append("\n| 絞り込み条件 | 対象馬数 | 単勝率 | 3着内率 | 単勝ROI |")
        lines.append("|------------|---------|--------|--------|---------|")
        for _, row in df_deep.iterrows():
            roi_mark = " ★" if row["単勝ROI"] >= 1.00 else (" ◎" if row["単勝ROI"] >= 0.90 else "")
            lines.append(
                f"| {row['絞り込み条件']} | {int(row['対象馬数'])} | "
                f"{row['単勝率']:.1%} | {row['3着内率']:.1%} | {row['単勝ROI']:.1%}{roi_mark} |"
            )
        lines.append("")

    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================


def main() -> None:
    """穴馬パターン分析のエントリーポイント。"""
    parser = argparse.ArgumentParser(description="指数下位馬券検出・穴馬パターン分析")
    parser.add_argument("--start", default="20240101", help="開始日 YYYYMMDD")
    parser.add_argument("--end", default="20261231", help="終了日 YYYYMMDD")
    parser.add_argument(
        "--min-index-rank", type=int, default=4, help="指数下位判定の閾値（デフォルト: 4位以降）"
    )
    parser.add_argument("--report", default=None, help="Markdownレポート出力ディレクトリ")
    args = parser.parse_args()

    # データ取得
    df = load_data(args.start, args.end)
    if df.empty:
        logger.error("データが取得できませんでした。期間や指数バージョンを確認してください。")
        return

    df = filter_valid(df)
    df = add_ranks(df)
    df = add_individual_index_ranks(df)
    df = compute_upside_score(df)

    # パターン分析
    patterns = analyze_upside_patterns(df)
    validation = validate_upside_score(df)

    # コンソール出力
    params = {
        "start_date": args.start,
        "end_date": args.end,
        "min_index_rank": args.min_index_rank,
    }
    report_text = print_report(patterns, validation, params)
    print(report_text)

    # ファイル出力
    if args.report:
        out_dir = Path(args.report)
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / f"upside_report_{args.start}_{args.end}.md"
        report_path.write_text(report_text, encoding="utf-8")
        logger.info(f"レポート出力: {report_path}")

        # CSVも出力
        upside_csv = out_dir / f"upside_cases_{args.start}_{args.end}.csv"
        upside_cases = df[df["upside_flag"]].copy()
        upside_cases.to_csv(upside_csv, index=False, encoding="utf-8-sig")
        logger.info(f"穴馬ケースCSV出力: {upside_csv} ({len(upside_cases):,} 件)")


if __name__ == "__main__":
    main()
