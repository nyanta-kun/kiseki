"""地方競馬 Analyst Agent — 穴馬パターン分析 + 交互作用項候補生成

穴馬の定義: 単勝オッズ10倍以上 × 3着以内

分析内容:
  1. 穴馬時の個別指数リフト分析（突出率・平均値比較）
  2. C(4,2)=6通りの交互作用項をスコアリングして上位N個を選定
  3. コース/距離帯/馬場状態別の穴馬率集計
  4. composite_index上位馬の条件別ROI（悪条件フィルター候補特定）
  5. 結果をJSONおよびコンソールに出力

使い方:
  uv run python scripts/chihou_analyst_agent.py --start 20240101 --end 20261231
  uv run python scripts/chihou_analyst_agent.py --start 20240101 --end 20261231 --top-n 6
  uv run python scripts/chihou_analyst_agent.py --start 20240101 --end 20261231 \\
      --out scripts/chihou_interaction_candidates.json --min-odds 10.0
"""

from __future__ import annotations

import argparse
import itertools
import json
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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.config import settings
from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_analyst_agent")

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

UPSIDE_ODDS_THRESHOLD = 10.0   # 穴馬判定: 単勝オッズ10倍以上
UPSIDE_FINISH_THRESHOLD = 3    # 穴馬判定: 3着以内
BANEI_COURSE = "83"            # ばんえい競馬コード（除外）

# 地方競馬の分析対象指数列
INDEX_COLS = [
    "speed_index",
    "last3f_index",
    "jockey_index",
    "rotation_index",
    "place_ev_index",
]
INDEX_LABELS = {
    "speed_index":    "スピード",
    "last3f_index":   "後3F",
    "jockey_index":   "騎手",
    "rotation_index": "ローテ",
    "place_ev_index": "複勝EV",
}

# 主要競馬場（分析を詳細化する場所）
MAJOR_COURSES = ["大井", "園田", "名古屋", "川崎", "船橋", "浦和", "門別", "水沢"]


# ============================================================
# データ取得
# ============================================================


def load_data(
    start_date: str,
    end_date: str,
    version: int = CHIHOU_COMPOSITE_VERSION,
    courses: list[str] | None = None,
) -> pd.DataFrame:
    """算出指数・レース結果・レース情報を結合取得する。

    ばんえい競馬（course='83'）は除外する。
    place_odds は約3%しかないため win_odds のみ確実に取得する。

    Args:
        start_date: 開始日 (YYYYMMDD)
        end_date: 終了日 (YYYYMMDD)
        version: chihou.calculated_indices バージョン
        courses: 競馬場名リスト（Noneで全場）例: ["盛岡", "高知", "笠松", "佐賀"]

    Returns:
        DataFrame: 全馬の指数・結果・レース情報を結合したデータ
    """
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    course_filter = ""
    if courses:
        names = ", ".join(f"'{c}'" for c in courses)
        course_filter = f"  AND r.course_name IN ({names})"

    sql = text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course          AS course,
    r.course_name     AS course_name,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    r.condition       AS condition,
    r.grade           AS grade,
    r.race_type_code  AS race_type_code,
    ci.horse_id       AS horse_id,
    ci.composite_index    AS composite_index,
    ci.speed_index        AS speed_index,
    ci.last3f_index       AS last3f_index,
    ci.jockey_index       AS jockey_index,
    ci.rotation_index     AS rotation_index,
    ci.place_ev_index     AS place_ev_index,
    ci.win_probability    AS win_probability,
    ci.place_probability  AS place_probability,
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds,
    rr.win_popularity     AS win_popularity,
    rr.place_odds         AS place_odds,
    rr.horse_number       AS horse_number
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :sd AND :ed
  AND ci.version = {version}
  AND r.course != '{BANEI_COURSE}'
{course_filter}
ORDER BY r.date, r.id, ci.horse_id
""")

    with Session(engine) as db:
        result = db.execute(sql, {"sd": sd, "ed": ed})
        rows = result.fetchall()
        columns = list(result.keys())

    if not rows:
        logger.warning("データなし")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=columns)

    numeric_cols = [
        "composite_index",
        "win_probability",
        "place_probability",
        "win_odds",
        "win_popularity",
        "place_odds",
        "finish_position",
        "head_count",
        "distance",
        "abnormality_code",
    ] + INDEX_COLS

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    logger.info(f"取得: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


def filter_valid(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """異常コード・着順NULL・composite_index NULL・少頭数レースを除外する。

    Args:
        df: load_data の戻り値
        min_runners: レースあたりの最低頭数

    Returns:
        フィルタ済み DataFrame
    """
    bad = df[
        (df["abnormality_code"] > 0)
        | df["finish_position"].isna()
        | df["composite_index"].isna()
    ]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()
    counts = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(counts[counts >= min_runners].index)].copy()
    logger.info(f"フィルタ後: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


# ============================================================
# ランク付け・フラグ設定
# ============================================================


def add_ranks(
    df: pd.DataFrame,
    odds_threshold: float = UPSIDE_ODDS_THRESHOLD,
) -> pd.DataFrame:
    """composite_index のレース内ランクと穴馬フラグを付与する。

    Args:
        df: filter_valid 済みデータ
        odds_threshold: 穴馬判定の単勝オッズ閾値

    Returns:
        "index_rank", "odds_upside_flag" 列を追加した DataFrame
    """
    df = df.copy()
    df["index_rank"] = df.groupby("race_id", group_keys=False)["composite_index"].rank(
        ascending=False, method="min"
    )
    df["odds_upside_flag"] = (df["win_odds"] >= odds_threshold) & (
        df["finish_position"] <= UPSIDE_FINISH_THRESHOLD
    )
    df["index_upside_flag"] = (df["index_rank"] >= 4) & (df["finish_position"] <= 3)
    return df


def add_individual_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """各個別指数のレース内ランクを追加する。

    Args:
        df: add_ranks 済みデータ

    Returns:
        "{col}_rank" 列を追加した DataFrame
    """
    df = df.copy()
    for col in INDEX_COLS:
        if col in df.columns:
            df[f"{col}_rank"] = df.groupby("race_id")[col].rank(ascending=False, method="min")
    return df


# ============================================================
# 穴馬プロファイル分析
# ============================================================


def analyze_upside_profile(df: pd.DataFrame) -> pd.DataFrame:
    """穴馬的中時 vs 外れ(指数下位)時の各指数平均値・突出率を比較する。

    Args:
        df: add_ranks / add_individual_ranks 済みデータ

    Returns:
        DataFrame: 指数別リフト分析結果（突出率リフト降順）
    """
    upside = df[df["odds_upside_flag"]].copy()
    ctrl = df[(df["index_rank"] >= 4) & (df["finish_position"] > 3)].copy()

    rows = []
    for col in INDEX_COLS:
        if col not in df.columns:
            continue
        rank_col = f"{col}_rank"

        val_up = upside[col].dropna().mean() if len(upside) > 0 else float("nan")
        val_ctrl = ctrl[col].dropna().mean() if len(ctrl) > 0 else float("nan")
        all_mean = df[col].dropna().mean()

        if rank_col in upside.columns:
            prom_up = (upside[rank_col] < upside["index_rank"]).mean()
            prom_ctrl = (ctrl[rank_col] < ctrl["index_rank"]).mean() if len(ctrl) > 0 else 0.0
            lift = prom_up - prom_ctrl
        else:
            prom_up = prom_ctrl = lift = float("nan")

        rows.append(
            {
                "指数名": INDEX_LABELS.get(col, col),
                "col": col,
                "穴馬的中_平均": round(val_up, 2) if not np.isnan(val_up) else None,
                "外れ_平均": round(val_ctrl, 2) if not np.isnan(val_ctrl) else None,
                "全体_平均": round(all_mean, 2),
                "平均差(穴-外れ)": round(val_up - val_ctrl, 2)
                if not (np.isnan(val_up) or np.isnan(val_ctrl))
                else None,
                "穴馬時_突出率": round(prom_up, 4) if not np.isnan(prom_up) else None,
                "外れ時_突出率": round(prom_ctrl, 4) if not np.isnan(prom_ctrl) else None,
                "突出率リフト": round(lift, 4) if not np.isnan(lift) else None,
            }
        )

    return pd.DataFrame(rows).sort_values("突出率リフト", ascending=False, na_position="last")


# ============================================================
# 交互作用項スコアリング
# ============================================================


def score_interactions(
    df: pd.DataFrame,
    top_n: int = 6,
    min_upside_count: int = 20,
) -> list[dict]:
    """C(4,2)=6通りの交互作用項をスコアリングして上位 N 個を返す。

    スコア = 標準化リフト:
        z = f_i * f_j / 100
        lift = E[z | upside] - E[z | ctrl]
        score = lift / std(z)

    Args:
        df: add_ranks / add_individual_ranks 済みデータ
        top_n: 返す候補数
        min_upside_count: 穴馬ケースの最低必要件数

    Returns:
        list[dict]: スコア降順の交互作用項候補
    """
    upside_mask = df["odds_upside_flag"].fillna(False)
    ctrl_mask = (df["index_rank"] >= 4) & (df["finish_position"] > 3)

    n_upside = int(upside_mask.sum())
    if n_upside < min_upside_count:
        logger.warning(f"穴馬ケース数が不足: {n_upside} < {min_upside_count}")
        return []

    available = [
        c for c in INDEX_COLS if c in df.columns and df[c].notna().mean() >= 0.5
    ]

    candidates = []
    for col_i, col_j in itertools.combinations(available, 2):
        z = df[col_i] * df[col_j] / 100.0

        valid = z.notna() & df["finish_position"].notna()
        if valid.sum() < 100:
            continue

        z_up = z[upside_mask & valid]
        z_ctrl = z[ctrl_mask & valid]
        if len(z_up) < 10 or len(z_ctrl) < 10:
            continue

        mean_up = float(z_up.mean())
        mean_ctrl = float(z_ctrl.mean())
        lift = mean_up - mean_ctrl
        std_z = float(z[valid].std())
        if std_z < 1e-6:
            continue

        score = lift / std_z
        corr = float(z[valid].corr(upside_mask[valid].astype(float)))

        candidates.append(
            {
                "feature": f"{col_i}*{col_j}",
                "col_i": col_i,
                "col_j": col_j,
                "label_i": INDEX_LABELS.get(col_i, col_i),
                "label_j": INDEX_LABELS.get(col_j, col_j),
                "mean_upside": round(mean_up, 3),
                "mean_ctrl": round(mean_ctrl, 3),
                "lift": round(lift, 4),
                "score": round(score, 4),
                "corr": round(corr, 4),
                "n_upside": n_upside,
                "n_ctrl": int(len(z_ctrl)),
            }
        )

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


# ============================================================
# 条件別穴馬率分析
# ============================================================


def analyze_by_condition(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """コース・距離帯・馬場状態別の穴馬率を集計する。

    Args:
        df: add_ranks 済みデータ

    Returns:
        dict: 各条件軸ごとの集計 DataFrame
    """
    low_rank = df[df["index_rank"] >= 4].copy()
    results: dict[str, pd.DataFrame] = {}

    # コース別
    if "course_name" in df.columns:
        stats = (
            low_rank.groupby("course_name", dropna=False)
            .agg(total=("odds_upside_flag", "count"), upside=("odds_upside_flag", "sum"))
            .assign(upside_rate=lambda x: (x["upside"] / x["total"]).round(4))
            .sort_values("upside_rate", ascending=False)
            .reset_index()
        )
        results["course_name"] = stats

    # 馬場状態別
    for col in ("condition", "surface", "grade"):
        if col not in df.columns:
            continue
        stats = (
            low_rank.groupby(col, dropna=False)
            .agg(total=("odds_upside_flag", "count"), upside=("odds_upside_flag", "sum"))
            .assign(upside_rate=lambda x: (x["upside"] / x["total"]).round(4))
            .sort_values("upside_rate", ascending=False)
            .reset_index()
        )
        results[col] = stats

    # 距離帯別（地方は短距離中心）
    if "distance" in df.columns:
        bins = [0, 1000, 1400, 1800, 9999]
        labels = ["超短距離(～1000)", "短距離(1001-1400)", "マイル(1401-1800)", "中長距離(1801+)"]
        lr2 = low_rank.copy()
        lr2["dist_band"] = pd.cut(lr2["distance"], bins=bins, labels=labels)
        results["distance_band"] = (
            lr2.groupby("dist_band", observed=True)
            .agg(total=("odds_upside_flag", "count"), upside=("odds_upside_flag", "sum"))
            .assign(upside_rate=lambda x: (x["upside"] / x["total"]).round(4))
            .sort_values("upside_rate", ascending=False)
            .reset_index()
        )

    return results


# ============================================================
# 悪条件フィルター
# ============================================================


def analyze_bad_conditions(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """composite_index 1位馬の条件別単勝ROIを集計する。

    Args:
        df: add_ranks 済みデータ

    Returns:
        dict: 各条件軸ごとのROI集計 DataFrame（ROI昇順）
    """
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    top1 = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    if top1.empty:
        return {}

    results: dict[str, pd.DataFrame] = {}
    for col in ("course_name", "condition", "surface", "grade"):
        if col not in top1.columns:
            continue
        rows = []
        for val, grp in top1.groupby(col, dropna=False):
            n = len(grp)
            wins = (grp["finish_position"] == 1).sum()
            roi = grp.loc[grp["finish_position"] == 1, "win_odds"].sum() / n * 100 if n > 0 else 0.0
            rows.append(
                {
                    col: val,
                    "bets": n,
                    "wins": int(wins),
                    "win_rate_pct": round(wins / n * 100, 1),
                    "roi_pct": round(roi, 1),
                }
            )
        results[col] = (
            pd.DataFrame(rows).sort_values("roi_pct").reset_index(drop=True)
        )

    return results


# ============================================================
# 穴馬ROI集計
# ============================================================


def compute_upside_roi(
    df: pd.DataFrame,
    score_col: str = "composite_index",
    top_n: int = 3,
    odds_threshold: float = UPSIDE_ODDS_THRESHOLD,
) -> dict:
    """upside スコア上位 top_n 馬のうちオッズ閾値以上の馬の単勝ROIを計算する。

    place_odds は地方競馬では約3%しか存在しないため win_roi のみ算出する。

    Args:
        df: add_ranks 済みデータ
        score_col: ランク付けに使うスコア列
        top_n: レースあたり上位何頭を購入対象とするか
        odds_threshold: 単勝オッズの最低閾値

    Returns:
        dict: bets, win_roi_pct, hit_rate, upside_win_roi_pct（win_roi_pctのエイリアス）
    """
    df = df.copy()
    df["_score_rank"] = df.groupby("race_id")[score_col].rank(ascending=False, method="min")
    candidates = df[(df["_score_rank"] <= top_n) & (df["win_odds"] >= odds_threshold)].copy()

    if candidates.empty:
        return {
            "bets": 0,
            "win_roi_pct": 0.0,
            "upside_win_roi_pct": 0.0,
            "upside_place_roi_pct": None,
            "hit_rate": 0.0,
        }

    n = len(candidates)
    wins = candidates[candidates["finish_position"] == 1]
    places = candidates[candidates["finish_position"] <= 3]

    win_roi = round(wins["win_odds"].sum() / n * 100, 1)

    # place_odds は少ないため win_roi にフォールバック
    place_roi: float | None = None
    if (
        "place_odds" in candidates.columns
        and candidates["place_odds"].notna().mean() >= 0.2
    ):
        place_roi = round(places["place_odds"].sum() / n * 100, 1)

    hit_races = places["race_id"].nunique()
    total_races = candidates["race_id"].nunique()
    hit_rate = round(hit_races / total_races, 4) if total_races > 0 else 0.0

    return {
        "bets": n,
        "win_roi_pct": win_roi,
        "upside_win_roi_pct": win_roi,
        "upside_place_roi_pct": place_roi,
        "hit_rate": hit_rate,
    }


# ============================================================
# 全体標準指標
# ============================================================


def compute_standard_metrics(df: pd.DataFrame) -> dict:
    """composite_index 1位馬の単勝ROI・3着内率を計算する。

    Args:
        df: add_ranks 済みデータ

    Returns:
        dict: place_rate_pct, win_roi_pct
    """
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    top1 = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    if top1.empty:
        return {"place_rate_pct": 0.0, "win_roi_pct": 0.0}

    place_rate = (top1["finish_position"] <= 3).mean() * 100
    win_roi = top1.loc[top1["finish_position"] == 1, "win_odds"].sum() / len(top1) * 100
    return {
        "place_rate_pct": round(place_rate, 1),
        "win_roi_pct": round(win_roi, 1),
    }


# ============================================================
# メイン分析ランナー
# ============================================================


def run_analysis(
    df: pd.DataFrame,
    top_n: int = 6,
    odds_threshold: float = UPSIDE_ODDS_THRESHOLD,
) -> dict:
    """全分析を実行して結果 dict を返す。

    Args:
        df: load_data 取得済みデータ（filter_valid 未適用可）
        top_n: 交互作用項候補の上位件数（最大6通り）
        odds_threshold: 穴馬オッズ閾値

    Returns:
        dict:
          - "upside_profile": DataFrame
          - "top_interactions": list[dict]
          - "by_condition": dict[str, DataFrame]
          - "bad_conditions": dict[str, DataFrame]
          - "baseline_upside_roi": dict
          - "baseline_standard": dict
          - "meta": dict
    """
    df = filter_valid(df)
    df = add_ranks(df, odds_threshold=odds_threshold)
    df = add_individual_ranks(df)

    n_upside = int(df["odds_upside_flag"].sum())
    n_eligible = int((df["win_odds"] >= odds_threshold).sum())
    upside_rate = n_upside / n_eligible if n_eligible > 0 else 0.0

    logger.info(
        f"穴馬ケース(オッズ{odds_threshold}倍以上×3着内): {n_upside:,} 件 "
        f"/ 対象馬: {n_eligible:,} 件 ({upside_rate:.1%})"
    )

    return {
        "upside_profile": analyze_upside_profile(df),
        "top_interactions": score_interactions(df, top_n=top_n),
        "by_condition": analyze_by_condition(df),
        "bad_conditions": analyze_bad_conditions(df),
        "baseline_upside_roi": compute_upside_roi(
            df, score_col="composite_index", odds_threshold=odds_threshold
        ),
        "baseline_standard": compute_standard_metrics(df),
        "meta": {
            "n_races": int(df["race_id"].nunique()),
            "n_rows": len(df),
            "n_upside": n_upside,
            "n_eligible": n_eligible,
            "upside_rate": round(upside_rate, 4),
            "odds_threshold": odds_threshold,
        },
    }


# ============================================================
# コンソール出力
# ============================================================


def print_report(results: dict, odds_threshold: float = UPSIDE_ODDS_THRESHOLD) -> None:
    """分析結果をコンソールに出力する。"""
    meta = results["meta"]
    std = results["baseline_standard"]
    base = results["baseline_upside_roi"]

    print(f"\n{'=' * 68}")
    print(f"  地方競馬 Analyst Agent — 穴馬パターン分析レポート")
    print(f"  対象: {meta['n_races']:,} レース / {meta['n_rows']:,} 頭")
    print(f"  穴馬定義: 単勝オッズ {odds_threshold:.0f}倍以上 × 3着以内")
    print(f"  穴馬ケース数: {meta['n_upside']:,} ({meta['upside_rate']:.1%} of eligible)")
    print(f"{'=' * 68}")

    print(f"\n── 現行指数ベースライン")
    print(f"  composite_index 1位 3着内率: {std['place_rate_pct']:.1f}%")
    print(f"  composite_index 1位 単勝ROI: {std['win_roi_pct']:.1f}%")

    print(
        f"\n── 穴馬ROI (composite_index 上位3頭 × オッズ{odds_threshold:.0f}倍以上 全頭購入)"
    )
    print(f"  対象馬数: {base['bets']:,}")
    print(f"  単勝ROI:  {base['win_roi_pct']:.1f}%")
    if base["upside_place_roi_pct"] is not None:
        print(f"  複勝ROI:  {base['upside_place_roi_pct']:.1f}%")
    else:
        print(f"  複勝ROI:  データ不足（win_roiにフォールバック）")
    print(f"  ヒット率: {base['hit_rate']:.1%}")

    print(f"\n── 個別指数リフト分析（突出率リフト降順）")
    print(
        f"\n  {'指数名':<10} {'穴馬的中_突出率':>14} {'外れ_突出率':>12}"
        f" {'突出率リフト':>12} {'平均差(穴-外れ)':>14}"
    )
    print("  " + "-" * 66)
    for _, row in results["upside_profile"].iterrows():
        lift = row["突出率リフト"]
        if lift is None:
            continue
        sign = "+" if lift > 0 else ""
        prom_up = row["穴馬時_突出率"] or 0.0
        prom_ctrl = row["外れ時_突出率"] or 0.0
        avg_diff = row["平均差(穴-外れ)"] or 0.0
        print(
            f"  {row['指数名']:<10} {prom_up:>14.1%} {prom_ctrl:>12.1%}"
            f" {sign}{lift:>11.1%} {avg_diff:>14.2f}"
        )

    inters = results["top_interactions"]
    print(f"\n── 交互作用項候補 Top {len(inters)}（スコア降順）")
    print(
        f"\n  {'特徴量':<28} {'スコア':>7} {'リフト':>7}"
        f" {'穴馬平均':>9} {'外れ平均':>9} {'相関':>7}"
    )
    print("  " + "-" * 74)
    for item in inters:
        label = f"{item['label_i']} × {item['label_j']}"
        print(
            f"  {label:<28} {item['score']:>7.4f} {item['lift']:>7.4f}"
            f" {item['mean_upside']:>9.2f} {item['mean_ctrl']:>9.2f} {item['corr']:>7.4f}"
        )

    # コース別分析
    by_cond = results.get("by_condition", {})
    if "course_name" in by_cond:
        cdf = by_cond["course_name"]
        print(f"\n── コース別穴馬率（指数下位4位以下 × オッズ{odds_threshold:.0f}倍以上 × 3着内）")
        print(f"\n  {'競馬場':<8} {'対象馬':>7} {'穴馬数':>7} {'穴馬率':>8}")
        print("  " + "-" * 36)
        for _, row in cdf.iterrows():
            print(
                f"  {str(row['course_name']):<8} {row['total']:>7,} {row['upside']:>7,}"
                f" {row['upside_rate']:>8.1%}"
            )

    # 悪条件フィルター
    bad = results.get("bad_conditions", {})
    if "course_name" in bad:
        bdf = bad["course_name"]
        print(f"\n── コース別ROI（composite_index 1位馬単勝、ROI昇順）")
        print(f"\n  {'競馬場':<8} {'賭け数':>7} {'勝率':>7} {'単勝ROI':>9}")
        print("  " + "-" * 38)
        for _, row in bdf.iterrows():
            print(
                f"  {str(row['course_name']):<8} {row['bets']:>7,}"
                f" {row['win_rate_pct']:>7.1f}% {row['roi_pct']:>9.1f}%"
            )


# ============================================================
# CLI エントリポイント
# ============================================================


def main() -> None:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="地方競馬 Analyst Agent — 穴馬パターン分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", default="20240101", help="開始日 (YYYYMMDD)")
    parser.add_argument("--end", default="20261231", help="終了日 (YYYYMMDD)")
    parser.add_argument("--top-n", type=int, default=6, help="交互作用項候補の上位件数（最大6）")
    parser.add_argument("--min-odds", type=float, default=10.0, help="穴馬判定オッズ閾値")
    parser.add_argument(
        "--version", type=int, default=CHIHOU_COMPOSITE_VERSION,
        help="calculated_indices バージョン"
    )
    parser.add_argument(
        "--out", default=None,
        help="交互作用項 JSON 出力パス（省略時: chihou_interaction_candidates.json）"
    )
    args = parser.parse_args()

    logger.info(f"地方競馬 Analyst Agent 開始: {args.start} 〜 {args.end}")
    df = load_data(args.start, args.end, version=args.version)
    if df.empty:
        print("データなし。終了します。")
        return

    results = run_analysis(df, top_n=args.top_n, odds_threshold=args.min_odds)
    print_report(results, odds_threshold=args.min_odds)

    out_path = Path(args.out) if args.out else _here.parent / "chihou_interaction_candidates.json"
    payload = {
        "meta": results["meta"],
        "top_interactions": results["top_interactions"],
        "baseline_upside_roi": results["baseline_upside_roi"],
        "baseline_standard": results["baseline_standard"],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info(f"交互作用項候補を保存: {out_path}")


if __name__ == "__main__":
    main()
