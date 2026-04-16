"""地方競馬 v7指数アーキテクチャ バックテスト

現行v6（加重合算）と提案v7（乗数方式）を比較検証する。

v6設計（現行）:
  composite_v6 = 0.349×speed + 0.240×last3f + 0.175×jockey
               + 0.118×rotation + 0.118×last_margin

v7設計（提案）:
  ability_base = weighted_avg(speed_index, last3f_index, last_margin_index)
  K_jockey     = clip(1.0 + (jockey_index  - 50) / 50 × max_jockey,  1-max_jockey,  1+max_jockey)
  K_rotation   = clip(1.0 + (rotation_index - 50) / 50 × max_rotation, 1-max_rotation, 1+max_rotation)
  composite_v7 = clip(ability_base × K_jockey × K_rotation, 0, 100)

評価指標:
  - 指数1位馬の勝率 (win_rate_rank1)
  - 指数1位馬の単勝ROI
  - 指数1位馬の3着以内率 (place_rate_rank1)
  - スピアマン順位相関（指数ランク vs 着順）
  - コース別ROI（大井/門別/園田/高知/盛岡など）
  - 低オッズカット別ROI（--odds-cut モード）

使い方:
  cd backend
  uv run python scripts/chihou_backtest_v7.py
  uv run python scripts/chihou_backtest_v7.py --start 20250101 --end 20260415
  uv run python scripts/chihou_backtest_v7.py --grid-search
  uv run python scripts/chihou_backtest_v7.py --grid-search --top 20 --by-course
  uv run python scripts/chihou_backtest_v7.py --odds-cut
  uv run python scripts/chihou_backtest_v7.py --odds-cut --by-course
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.config import settings
from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_backtest_v7")

engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

BANEI_COURSE = "83"

# グリッドサーチ対象パラメータ
# ability_base の内部重み (w_speed, w_last3f, w_last_margin) ※合計1.0に正規化
ABILITY_PARAM_SETS: list[tuple[float, float, float]] = [
    (0.5, 0.3, 0.2),   # 提案デフォルト
    (0.6, 0.3, 0.1),
    (0.5, 0.4, 0.1),
    (0.6, 0.2, 0.2),
    (0.4, 0.4, 0.2),
    (0.4, 0.3, 0.3),
    (0.5, 0.2, 0.3),
    (0.7, 0.2, 0.1),   # speed重視
    (0.5, 0.5, 0.0),   # last_margin除外
]

# 騎手乗数最大効果（±X%）
JOCKEY_MAX_VALS = [0.05, 0.08, 0.10, 0.12, 0.15]

# ローテ乗数最大効果（±X%）
ROTATION_MAX_VALS = [0.05, 0.08, 0.10, 0.12]

# デフォルト（非グリッドサーチ時）
DEFAULT_ABILITY = (0.5, 0.3, 0.2)
DEFAULT_JOCKEY_MAX = 0.10
DEFAULT_ROTATION_MAX = 0.08

# コース別ROI集計（プロンプト分類と同じ）
COURSE_GROUPS = {
    "最優先（盛岡・高知）":   ["盛岡", "高知"],
    "優先（笠松・佐賀）":    ["笠松", "佐賀"],
    "中程度（園田・川崎・水沢・浦和・名古屋）": ["園田", "川崎", "水沢", "浦和", "名古屋"],
    "消極（大井・門別・船橋・姫路・金沢）":     ["大井", "門別", "船橋", "姫路", "金沢"],
}


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def load_data(start_date: str, end_date: str, version: int = CHIHOU_COMPOSITE_VERSION) -> pd.DataFrame:
    """v6算出済み指数 + 実際の結果を一括取得する。"""
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    sql = text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course          AS course,
    r.course_name     AS course_name,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    ci.horse_id       AS horse_id,
    ci.speed_index        AS speed_index,
    ci.last3f_index       AS last3f_index,
    ci.jockey_index       AS jockey_index,
    ci.rotation_index     AS rotation_index,
    ci.last_margin_index  AS last_margin_index,
    ci.composite_index    AS composite_v6,
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds,
    rr.win_popularity     AS win_popularity,
    rr.horse_number       AS horse_number
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :sd AND :ed
  AND ci.version = {version}
  AND r.course != '{BANEI_COURSE}'
ORDER BY r.date, r.id, rr.horse_number
""")

    with Session(engine) as db:
        result = db.execute(sql, {"sd": sd, "ed": ed})
        rows = result.fetchall()
        columns = list(result.keys())

    if not rows:
        logger.warning("データなし: %s〜%s", start_date, end_date)
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=columns)

    numeric_cols = [
        "speed_index", "last3f_index", "jockey_index",
        "rotation_index", "last_margin_index", "composite_v6",
        "finish_position", "win_odds", "win_popularity",
        "head_count", "distance", "abnormality_code",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    logger.info("取得: %d 件 / %d レース", len(df), df["race_id"].nunique())
    return df


# ---------------------------------------------------------------------------
# v7 composite 計算
# ---------------------------------------------------------------------------

def compute_v7(
    df: pd.DataFrame,
    w_speed: float,
    w_last3f: float,
    w_last_margin: float,
    max_jockey: float,
    max_rotation: float,
) -> pd.Series:
    """v7乗数方式で composite_index を計算して返す。

    ability_base = weighted_avg(speed, last3f, last_margin)
    K_jockey     = clip(1 + (jockey - 50)/50 × max_jockey, ...)
    K_rotation   = clip(1 + (rotation - 50)/50 × max_rotation, ...)
    composite    = clip(ability_base × K_jockey × K_rotation, 0, 100)

    last_margin が NULL の場合は speed + last3f の2指数で正規化。
    """
    sp = df["speed_index"].fillna(50.0)
    l3 = df["last3f_index"].fillna(50.0)
    lm = df["last_margin_index"]  # NULL あり
    jk = df["jockey_index"].fillna(50.0)
    ro = df["rotation_index"].fillna(50.0)

    has_margin = lm.notna()

    # ability_base
    # last_margin がある行: w_speed+w_last3f+w_last_margin=1.0 で正規化済みの重みを使う
    # last_margin がない行: w_speed+w_last3f で再正規化
    ability = pd.Series(index=df.index, dtype=float)

    total_full = w_speed + w_last3f + w_last_margin
    total_no_margin = w_speed + w_last3f

    # last_margin あり
    mask = has_margin
    if mask.any():
        ability[mask] = (
            w_speed * sp[mask] + w_last3f * l3[mask] + w_last_margin * lm[mask]
        ) / total_full

    # last_margin なし
    mask = ~has_margin
    if mask.any():
        ability[mask] = (w_speed * sp[mask] + w_last3f * l3[mask]) / total_no_margin

    # 乗数
    def make_k(index_col: pd.Series, max_eff: float) -> pd.Series:
        k = 1.0 + (index_col - 50.0) / 50.0 * max_eff
        return k.clip(lower=1.0 - max_eff, upper=1.0 + max_eff)

    k_jockey = make_k(jk, max_jockey)
    k_rotation = make_k(ro, max_rotation)

    composite = (ability * k_jockey * k_rotation).clip(lower=0.0, upper=100.0)
    return composite


# ---------------------------------------------------------------------------
# データ前処理・フィルタリング
# ---------------------------------------------------------------------------

def filter_valid(df: pd.DataFrame) -> pd.DataFrame:
    """評価に使えないレース・馬を除外する。"""
    # 異常（出走取消・競走除外等）
    df = df[df["abnormality_code"] == 0].copy()
    # finish_positionが NULL
    df = df[df["finish_position"].notna()].copy()
    # 出走頭数 < 4（オッズの信頼性が低い）
    valid_races = df.groupby("race_id")["horse_id"].count()
    valid_races = valid_races[valid_races >= 4].index
    df = df[df["race_id"].isin(valid_races)].copy()
    return df


def add_ranks(df: pd.DataFrame, score_col: str, rank_col: str = "pred_rank") -> pd.DataFrame:
    """レース内でのスコア降順ランクを付与する（同点は dense rank）。"""
    df = df.copy()
    df[rank_col] = df.groupby("race_id")[score_col].rank(method="dense", ascending=False)
    return df


# ---------------------------------------------------------------------------
# 評価指標計算
# ---------------------------------------------------------------------------

def evaluate(df: pd.DataFrame, score_col: str, label: str = "") -> dict:
    """composite スコア列を使って各種指標を計算する。

    Returns:
        dict: 各種指標の辞書
    """
    df = add_ranks(df, score_col, "pred_rank")

    top1 = df[df["pred_rank"] == 1].copy()

    # --- 勝率 ---
    total_races = top1["race_id"].nunique()
    win_cnt = (top1["finish_position"] == 1).sum()
    win_rate = win_cnt / total_races if total_races > 0 else 0.0

    # --- 複勝率 ---
    place_cnt = (top1["finish_position"] <= 3).sum()
    place_rate = place_cnt / total_races if total_races > 0 else 0.0

    # --- 単勝ROI ---
    # win_odds があるレースのみ対象
    valid_odds = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    bets = len(valid_odds)
    payout = valid_odds.loc[valid_odds["finish_position"] == 1, "win_odds"].sum()
    roi_win = float(payout / bets * 100) if bets > 0 else 0.0

    # --- スピアマン順位相関 ---
    corrs = []
    for _, grp in df.groupby("race_id"):
        if len(grp) < 4:
            continue
        rho, _ = spearmanr(grp["pred_rank"], grp["finish_position"])
        if not np.isnan(rho):
            corrs.append(rho)
    spearman = float(np.mean(corrs)) if corrs else 0.0

    # --- 1位指数差（gap_1_2）の分布 ---
    idx_by_race = df.groupby("race_id")[score_col]
    gap_12 = (
        idx_by_race.apply(lambda g: sorted(g, reverse=True)[0] - sorted(g, reverse=True)[1]
                          if len(g) >= 2 else np.nan)
        .dropna()
    )
    gap_mean = float(gap_12.mean()) if len(gap_12) > 0 else 0.0

    return {
        "label":        label,
        "races":        int(total_races),
        "win_rate":     round(win_rate * 100, 2),
        "place_rate":   round(place_rate * 100, 2),
        "roi_win":      round(roi_win, 1),
        "spearman":     round(spearman, 4),
        "gap_mean":     round(gap_mean, 2),
    }


def evaluate_by_course(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """コース別のROI・勝率を計算する。"""
    df = add_ranks(df, score_col, "pred_rank")
    top1 = df[df["pred_rank"] == 1].copy()

    rows = []
    for course, grp in top1.groupby("course_name"):
        total = grp["race_id"].nunique()
        wins = (grp["finish_position"] == 1).sum()
        places = (grp["finish_position"] <= 3).sum()
        valid = grp[grp["win_odds"].notna() & (grp["win_odds"] > 0)]
        bets = len(valid)
        payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
        roi = float(payout / bets * 100) if bets > 0 else 0.0
        rows.append({
            "course_name": course,
            "races": total,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
            "place_rate": round(places / total * 100, 1) if total > 0 else 0.0,
            "roi_win": round(roi, 1),
        })

    return pd.DataFrame(rows).sort_values("roi_win", ascending=False)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def run_single(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """単一パラメータセットで v6 vs v7 を比較する。"""
    w_speed, w_last3f, w_last_margin = DEFAULT_ABILITY
    max_jockey = DEFAULT_JOCKEY_MAX
    max_rotation = DEFAULT_ROTATION_MAX

    df["composite_v7"] = compute_v7(
        df, w_speed, w_last3f, w_last_margin, max_jockey, max_rotation
    )

    v6 = evaluate(df, "composite_v6", label="v6（現行加重合算）")
    v7 = evaluate(df, "composite_v7", label=f"v7（乗数方式 speed={w_speed} l3f={w_last3f} lm={w_last_margin} jk±{int(max_jockey*100)}% ro±{int(max_rotation*100)}%）")

    print("\n" + "=" * 70)
    print("地方競馬 v6 vs v7 バックテスト結果")
    print(f"期間: {args.start} 〜 {args.end}")
    print("=" * 70)

    header = f"{'指標':<20} {'v6（現行）':>12} {'v7（乗数）':>12} {'差分':>10}"
    print(header)
    print("-" * 60)
    metrics = [
        ("レース数",     "races",       ""),
        ("勝率 (%)",    "win_rate",    "%"),
        ("複勝率 (%)",  "place_rate",  "%"),
        ("単勝ROI (%)", "roi_win",     "%"),
        ("スピアマンρ", "spearman",    ""),
        ("平均gap_1_2", "gap_mean",    "pt"),
    ]
    for name, key, unit in metrics:
        v6v = v6[key]
        v7v = v7[key]
        diff = (v7v - v6v) if isinstance(v6v, (int, float)) else "—"
        sign = "+" if isinstance(diff, float) and diff > 0 else ""
        print(f"{name:<20} {str(v6v):>12} {str(v7v):>12} {sign}{diff:>8}{unit}")

    if args.by_course:
        print("\n--- コース別 単勝ROI 比較 ---")
        c6 = evaluate_by_course(df, "composite_v6").set_index("course_name")
        c7 = evaluate_by_course(df, "composite_v7").set_index("course_name")
        merged = c6[["races", "win_rate", "roi_win"]].rename(
            columns={"win_rate": "v6_win%", "roi_win": "v6_roi%"}
        ).join(c7[["win_rate", "roi_win"]].rename(
            columns={"win_rate": "v7_win%", "roi_win": "v7_roi%"}
        ))
        merged["ROI差"] = (merged["v7_roi%"] - merged["v6_roi%"]).round(1)
        merged = merged.sort_values("v6_roi%", ascending=False)
        print(merged.to_string())


def run_grid_search(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """グリッドサーチで最適パラメータを探索する。"""
    total_params = len(ABILITY_PARAM_SETS) * len(JOCKEY_MAX_VALS) * len(ROTATION_MAX_VALS)
    logger.info("グリッドサーチ開始: %d パターン", total_params)

    # v6 ベースライン
    baseline = evaluate(df, "composite_v6", label="v6_baseline")
    logger.info("v6 baseline: win_rate=%.2f%%, roi=%.1f%%, spearman=%.4f",
                baseline["win_rate"], baseline["roi_win"], baseline["spearman"])

    results = []
    for idx, (ability, max_jk, max_ro) in enumerate(
        itertools.product(ABILITY_PARAM_SETS, JOCKEY_MAX_VALS, ROTATION_MAX_VALS), 1
    ):
        w_sp, w_l3, w_lm = ability
        col = f"_v7_{idx}"
        df[col] = compute_v7(df, w_sp, w_l3, w_lm, max_jk, max_ro)
        metrics = evaluate(df, col)
        metrics.update({
            "w_speed":   w_sp,
            "w_last3f":  w_l3,
            "w_margin":  w_lm,
            "max_jockey":   max_jk,
            "max_rotation": max_ro,
        })
        results.append(metrics)

        # 一時列を削除してメモリ節約
        df.drop(columns=[col], inplace=True)

        if idx % 20 == 0:
            logger.info("  %d / %d 完了", idx, total_params)

    result_df = pd.DataFrame(results)

    # ROI昇順ソートで上位表示
    result_df = result_df.sort_values("roi_win", ascending=False)

    print("\n" + "=" * 80)
    print(f"グリッドサーチ結果（上位 {args.top} 件）  期間: {args.start} 〜 {args.end}")
    print("=" * 80)
    print(f"[v6 baseline] win_rate={baseline['win_rate']}%  roi={baseline['roi_win']}%  spearman={baseline['spearman']}")
    print("-" * 80)

    top_df = result_df.head(args.top)[
        ["w_speed", "w_last3f", "w_margin", "max_jockey", "max_rotation",
         "win_rate", "place_rate", "roi_win", "spearman", "gap_mean"]
    ]
    print(top_df.to_string(index=False))

    # 最優良パラメータでコース別分析
    best = result_df.iloc[0]
    print(f"\n--- 最優良パラメータ (roi={best['roi_win']}%) でのコース別分析 ---")
    df["_best_v7"] = compute_v7(
        df, best["w_speed"], best["w_last3f"], best["w_margin"],
        best["max_jockey"], best["max_rotation"]
    )

    if args.by_course:
        c6 = evaluate_by_course(df, "composite_v6").set_index("course_name")
        c7 = evaluate_by_course(df, "_best_v7").set_index("course_name")
        merged = c6[["races", "win_rate", "roi_win"]].rename(
            columns={"win_rate": "v6_win%", "roi_win": "v6_roi%"}
        ).join(c7[["win_rate", "roi_win"]].rename(
            columns={"win_rate": "v7_win%", "roi_win": "v7_roi%"}
        ))
        merged["ROI差"] = (merged["v7_roi%"] - merged["v6_roi%"]).round(1)

        print("\n全コース別（v6_roi%降順）:")
        print(merged.sort_values("v6_roi%", ascending=False).to_string())

        print("\nコースグループ別サマリ:")
        for group_name, courses in COURSE_GROUPS.items():
            sub = merged[merged.index.isin(courses)]
            if sub.empty:
                continue
            # 件数加重平均ROI
            total_races = c6.loc[c6.index.isin(courses), "races"].sum()
            v6_roi_avg = (c6.loc[c6.index.isin(courses), "roi_win"] * c6.loc[c6.index.isin(courses), "races"]).sum() / total_races if total_races > 0 else 0
            v7_roi_avg = (c7.loc[c7.index.isin(courses), "roi_win"] * c7.loc[c7.index.isin(courses), "races"]).sum() / total_races if total_races > 0 else 0
            print(f"  {group_name}: v6_ROI={v6_roi_avg:.1f}%  v7_ROI={v7_roi_avg:.1f}%  差={v7_roi_avg-v6_roi_avg:+.1f}%  (n={total_races})")

    df.drop(columns=["_best_v7"], inplace=True, errors="ignore")

    # v6 との差分上位
    result_df["roi_diff"] = result_df["roi_win"] - baseline["roi_win"]
    print("\n--- v6比 ROI改善上位10件 ---")
    diff_top = result_df.sort_values("roi_diff", ascending=False).head(10)[
        ["w_speed", "w_last3f", "w_margin", "max_jockey", "max_rotation",
         "roi_win", "roi_diff", "win_rate", "spearman"]
    ]
    print(diff_top.to_string(index=False))


# ---------------------------------------------------------------------------
# CLI エントリーポイント
# ---------------------------------------------------------------------------

# 低オッズカット閾値一覧
ODDS_CUT_THRESHOLDS = [1.0, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]

# v7最優良パラメータ（グリッドサーチ結果より）
BEST_V7_PARAMS = dict(
    w_speed=0.5, w_last3f=0.2, w_last_margin=0.3,
    max_jockey=0.05, max_rotation=0.08,
)

# コース別 max_jockey 候補（グループ分け検証用）
JOCKEY_MAX_COURSE_CANDIDATES = [0.05, 0.08, 0.10, 0.12, 0.15]


def evaluate_odds_cut(
    df: pd.DataFrame,
    score_col: str,
    thresholds: list[float],
) -> pd.DataFrame:
    """低オッズカット閾値ごとの ROI・勝率・レース数を計算する。

    指数1位馬の単勝オッズが threshold 未満のレースをスキップし、
    残りのレースのみで購入した場合の指標を返す。

    Args:
        df: フィルタ済みデータ（filter_valid 適用後）
        score_col: composite 列名
        thresholds: 最低オッズ閾値のリスト（例: [1.0, 2.0, 3.0, ...]）

    Returns:
        閾値ごとの行を持つ DataFrame
    """
    df = add_ranks(df, score_col, "_tmp_rank")
    top1 = df[df["_tmp_rank"] == 1].copy()
    top1 = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]

    rows = []
    for thr in thresholds:
        cut = top1[top1["win_odds"] >= thr]
        total = len(cut)
        wins = (cut["finish_position"] == 1).sum()
        places = (cut["finish_position"] <= 3).sum()
        payout = cut.loc[cut["finish_position"] == 1, "win_odds"].sum()
        roi = float(payout / total * 100) if total > 0 else 0.0
        win_rate = float(wins / total * 100) if total > 0 else 0.0
        place_rate = float(places / total * 100) if total > 0 else 0.0
        skipped = len(top1) - total
        rows.append({
            "min_odds":    thr,
            "races":       total,
            "skipped":     skipped,
            "skip_rate":   round(skipped / len(top1) * 100, 1) if len(top1) > 0 else 0.0,
            "win_rate":    round(win_rate, 2),
            "place_rate":  round(place_rate, 2),
            "roi_win":     round(roi, 1),
        })

    df.drop(columns=["_tmp_rank"], inplace=True, errors="ignore")
    return pd.DataFrame(rows)


def evaluate_odds_cut_by_course(
    df: pd.DataFrame,
    score_col: str,
    threshold: float,
) -> pd.DataFrame:
    """特定の低オッズカット閾値でのコース別ROIを計算する。"""
    df = add_ranks(df, score_col, "_tmp_rank")
    top1 = df[(df["_tmp_rank"] == 1) & df["win_odds"].notna() & (df["win_odds"] > 0)].copy()
    top1 = top1[top1["win_odds"] >= threshold]

    rows = []
    for course, grp in top1.groupby("course_name"):
        total = len(grp)
        wins = (grp["finish_position"] == 1).sum()
        places = (grp["finish_position"] <= 3).sum()
        payout = grp.loc[grp["finish_position"] == 1, "win_odds"].sum()
        roi = float(payout / total * 100) if total > 0 else 0.0
        rows.append({
            "course_name": course,
            "races":       total,
            "win_rate":    round(wins / total * 100, 1) if total > 0 else 0.0,
            "place_rate":  round(places / total * 100, 1) if total > 0 else 0.0,
            "roi_win":     round(roi, 1),
        })

    df.drop(columns=["_tmp_rank"], inplace=True, errors="ignore")
    return pd.DataFrame(rows).sort_values("roi_win", ascending=False)


def run_course_grouping(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """コース別 max_jockey 最適化分析。

    各コースごとに max_jockey を 0.05〜0.15 で変えてROIを計算し、
    「馬能力重視（小）」と「騎手補正維持（大）」のどちらが優れるかを判定する。
    全体ROI・穴馬ROI（10倍以上）の両軸で評価する。
    """
    p = BEST_V7_PARAMS
    w_sp, w_l3, w_lm = p["w_speed"], p["w_last3f"], p["w_last_margin"]
    max_ro = p["max_rotation"]

    # 全コース一覧
    courses = sorted(df["course_name"].unique())

    print("\n" + "=" * 100)
    print(f"コース別 max_jockey 最適化分析  期間: {args.start} 〜 {args.end}")
    print(f"ability weights: speed={w_sp} last3f={w_l3} margin={w_lm}  rotation±{int(max_ro*100)}%")
    print("=" * 100)

    rows = []
    for course in courses:
        cdf = df[df["course_name"] == course].copy()
        if cdf["race_id"].nunique() < 20:
            continue

        # v6 ベースライン
        v6_ev = evaluate(cdf, "composite_v6")
        # 穴馬（10倍以上）v6
        cdf_top1_v6 = add_ranks(cdf, "composite_v6", "_r")
        top1_v6 = cdf_top1_v6[cdf_top1_v6["_r"] == 1]
        valid_v6 = top1_v6[top1_v6["win_odds"].notna() & (top1_v6["win_odds"] >= 10)]
        v6_upside_roi = float(valid_v6.loc[valid_v6["finish_position"] == 1, "win_odds"].sum()
                              / len(valid_v6) * 100) if len(valid_v6) > 0 else None
        v6_upside_n = len(valid_v6)
        cdf.drop(columns=["_r"], inplace=True, errors="ignore")

        # 各 max_jockey で v7 を評価
        best_jk_roi = None
        best_jk_val = None
        best_jk_upside_roi = None

        jk_results = {}
        for jk in JOCKEY_MAX_COURSE_CANDIDATES:
            cdf[f"_v7_{jk}"] = compute_v7(cdf, w_sp, w_l3, w_lm, jk, max_ro)
            ev = evaluate(cdf, f"_v7_{jk}")
            # 穴馬ROI
            cdf2 = add_ranks(cdf, f"_v7_{jk}", "_r2")
            top1_v7 = cdf2[cdf2["_r2"] == 1]
            valid_v7 = top1_v7[top1_v7["win_odds"].notna() & (top1_v7["win_odds"] >= 10)]
            upside_roi = float(valid_v7.loc[valid_v7["finish_position"] == 1, "win_odds"].sum()
                               / len(valid_v7) * 100) if len(valid_v7) > 0 else None
            upside_n = len(valid_v7)
            cdf.drop(columns=["_r2", f"_v7_{jk}"], inplace=True, errors="ignore")

            jk_results[jk] = {
                "roi": ev["roi_win"],
                "win_rate": ev["win_rate"],
                "upside_roi": upside_roi,
                "upside_n": upside_n,
            }
            # 総合スコア（全体ROI+穴馬ROIを合算して最良を選ぶ）
            upside_score = upside_roi if upside_roi else 0.0
            score = ev["roi_win"] + upside_score * 0.5  # 穴馬に半分の重み
            if best_jk_roi is None or score > (jk_results.get("_best_score", -999)):
                jk_results["_best_score"] = score
                best_jk_roi = ev["roi_win"]
                best_jk_val = jk
                best_jk_upside_roi = upside_roi

        rows.append({
            "course":       course,
            "races":        int(v6_ev["races"]),
            "v6_roi":       v6_ev["roi_win"],
            "v6_win%":      v6_ev["win_rate"],
            "v6_up_roi":    round(v6_upside_roi, 1) if v6_upside_roi else None,
            "v6_up_n":      v6_upside_n,
            **{f"v7_jk{int(jk*100)}_roi": jk_results[jk]["roi"] for jk in JOCKEY_MAX_COURSE_CANDIDATES},
            **{f"v7_jk{int(jk*100)}_up":  round(jk_results[jk]["upside_roi"], 1) if jk_results[jk]["upside_roi"] else None
               for jk in JOCKEY_MAX_COURSE_CANDIDATES},
            "best_jk":      best_jk_val,
            "best_v7_roi":  best_jk_roi,
            "best_up_roi":  round(best_jk_upside_roi, 1) if best_jk_upside_roi else None,
        })

    result_df = pd.DataFrame(rows)

    # ---- 全体ROI差でソート ----
    result_df["roi_diff"] = result_df["best_v7_roi"] - result_df["v6_roi"]

    print("\n【コース別 最適 max_jockey と ROI比較（全体ROI差順）】")
    print(f"{'コース':<8} {'N':>5} {'v6ROI':>7} {'最適jk':>8}"
          f" {'v7ROI':>7} {'ROI差':>7}"
          f" {'v6穴馬':>8} {'v7穴馬':>8} {'穴馬差':>7} {'判定':>12}")
    print("-" * 95)

    for _, r in result_df.sort_values("roi_diff", ascending=False).iterrows():
        roi_diff = r["roi_diff"]
        up_diff = (r["best_up_roi"] or 0) - (r["v6_up_roi"] or 0) if r["v6_up_roi"] else None
        sign = "+" if roi_diff > 0 else ""
        up_sign = "+" if up_diff and up_diff > 0 else ""

        # 判定ロジック
        if roi_diff > 2 and (up_diff is None or up_diff > -20):
            verdict = "→ A（馬能力）"
        elif roi_diff < -2 or (up_diff is not None and up_diff < -30):
            verdict = "→ B（騎手維持）"
        else:
            verdict = "→ 中間（要確認）"

        print(
            f"{r['course']:<8} {r['races']:>5}"
            f" {r['v6_roi']:>6.1f}%"
            f" {r['best_jk']:>7.2f}"
            f" {r['best_v7_roi']:>6.1f}%"
            f" {sign}{roi_diff:>5.1f}%"
            f" {str(r['v6_up_roi'] or '—'):>7}"
            f" {str(r['best_up_roi'] or '—'):>8}"
            f" {(up_sign+str(round(up_diff,1)) if up_diff is not None else '—'):>7}"
            f" {verdict:>14}"
        )

    # ---- max_jockey 別の詳細テーブル ----
    print("\n\n【コース別 × max_jockey 全体ROI 詳細】")
    header = f"{'コース':<8} {'v6ROI':>7}"
    for jk in JOCKEY_MAX_COURSE_CANDIDATES:
        header += f" {'jk±'+str(int(jk*100))+'%':>8}"
    print(header)
    print("-" * 65)
    for _, r in result_df.sort_values("roi_diff", ascending=False).iterrows():
        line = f"{r['course']:<8} {r['v6_roi']:>6.1f}%"
        for jk in JOCKEY_MAX_COURSE_CANDIDATES:
            v = r[f"v7_jk{int(jk*100)}_roi"]
            mark = " *" if jk == r["best_jk"] else "  "
            line += f" {v:>6.1f}%{mark}"
        print(line)

    print("\n\n【コース別 × max_jockey 穴馬ROI（10倍以上）詳細】")
    header2 = f"{'コース':<8} {'v6穴馬':>8}"
    for jk in JOCKEY_MAX_COURSE_CANDIDATES:
        header2 += f" {'jk±'+str(int(jk*100))+'%':>8}"
    print(header2)
    print("-" * 65)
    for _, r in result_df.sort_values("roi_diff", ascending=False).iterrows():
        v6u = r["v6_up_roi"]
        line = f"{r['course']:<8} {str(v6u or '—'):>7}"
        for jk in JOCKEY_MAX_COURSE_CANDIDATES:
            v = r[f"v7_jk{int(jk*100)}_up"]
            mark = " *" if jk == r["best_jk"] else "  "
            line += f" {str(v or '—'):>7}{mark}"
        print(line)

    # ---- グループ提案 ----
    grp_a = result_df[result_df["roi_diff"] > 2]["course"].tolist()
    grp_b = result_df[result_df["roi_diff"] < -2]["course"].tolist()
    grp_mid = result_df[result_df["roi_diff"].between(-2, 2)]["course"].tolist()

    print("\n\n【暫定グループ提案（全体ROI差±2%を境界）】")
    print(f"グループA（馬能力重視 jk±5%）: {grp_a}")
    print(f"グループB（騎手補正維持）:       {grp_b}")
    print(f"中間（±2%以内、要追加検討）:    {grp_mid}")


def run_odds_cut(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """低オッズカット分析: v6 vs v7（最優良）を閾値ごとに比較する。"""
    # v7最優良パラメータで composite を計算
    p = BEST_V7_PARAMS
    df["composite_v7"] = compute_v7(
        df, p["w_speed"], p["w_last3f"], p["w_last_margin"],
        p["max_jockey"], p["max_rotation"],
    )

    c6 = evaluate_odds_cut(df, "composite_v6", ODDS_CUT_THRESHOLDS)
    c7 = evaluate_odds_cut(df, "composite_v7", ODDS_CUT_THRESHOLDS)

    print("\n" + "=" * 80)
    print(f"低オッズカット別 ROI比較  期間: {args.start} 〜 {args.end}")
    print(f"v7パラメータ: speed={p['w_speed']} last3f={p['w_last3f']} margin={p['w_last_margin']}"
          f"  jk±{int(p['max_jockey']*100)}%  ro±{int(p['max_rotation']*100)}%")
    print("=" * 80)
    print(f"{'最低ｵｯｽﾞ':>8} {'購入数':>7} {'除外数':>7} {'除外率':>7}"
          f" │ {'v6勝率':>7} {'v6ROI':>7} │ {'v7勝率':>7} {'v7ROI':>7} │ {'ROI差':>7}")
    print("-" * 80)

    for (_, r6), (_, r7) in zip(c6.iterrows(), c7.iterrows()):
        diff = r7["roi_win"] - r6["roi_win"]
        sign = "+" if diff > 0 else ""
        print(
            f"{r6['min_odds']:>8.1f} {r6['races']:>7} {r6['skipped']:>7} {r6['skip_rate']:>6.1f}%"
            f" │ {r6['win_rate']:>6.1f}% {r6['roi_win']:>6.1f}%"
            f" │ {r7['win_rate']:>6.1f}% {r7['roi_win']:>6.1f}%"
            f" │ {sign}{diff:>5.1f}%"
        )

    # 最も ROI が高い閾値を特定
    best_v6 = c6.loc[c6["roi_win"].idxmax()]
    best_v7 = c7.loc[c7["roi_win"].idxmax()]
    print("\n" + "-" * 80)
    print(f"v6 ROI最高: min_odds={best_v6['min_odds']:.1f}倍  ROI={best_v6['roi_win']}%"
          f"  勝率={best_v6['win_rate']}%  購入={int(best_v6['races'])}レース")
    print(f"v7 ROI最高: min_odds={best_v7['min_odds']:.1f}倍  ROI={best_v7['roi_win']}%"
          f"  勝率={best_v7['win_rate']}%  購入={int(best_v7['races'])}レース")

    if args.by_course:
        # 両者のROI最高閾値でコース別を表示
        for label, score_col, best_thr in [
            ("v6", "composite_v6", float(best_v6["min_odds"])),
            ("v7", "composite_v7", float(best_v7["min_odds"])),
        ]:
            print(f"\n--- {label}（min_odds≥{best_thr:.1f}倍カット）コース別 ROI ---")
            cc = evaluate_odds_cut_by_course(df, score_col, best_thr)
            print(cc.to_string(index=False))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="地方競馬 v7指数アーキテクチャ バックテスト")
    p.add_argument("--start",  default="20250101", help="開始日 YYYYMMDD（デフォルト: 20250101）")
    p.add_argument("--end",    default="20260415", help="終了日 YYYYMMDD（デフォルト: 20260415）")
    p.add_argument("--grid-search",    action="store_true", help="グリッドサーチを実行する")
    p.add_argument("--odds-cut",       action="store_true", help="低オッズカット分析を実行する")
    p.add_argument("--course-grouping",action="store_true", help="コース別 max_jockey 最適化分析を実行する")
    p.add_argument("--by-course",      action="store_true", help="コース別分析を出力する")
    p.add_argument("--top", type=int, default=20, help="グリッドサーチ上位表示件数（デフォルト: 20）")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("データ取得中: %s 〜 %s", args.start, args.end)
    df_raw = load_data(args.start, args.end)
    if df_raw.empty:
        logger.error("データが取得できませんでした")
        sys.exit(1)

    df = filter_valid(df_raw)
    logger.info("フィルタ後: %d 件 / %d レース", len(df), df["race_id"].nunique())

    if args.grid_search:
        run_grid_search(df, args)
    elif args.odds_cut:
        run_odds_cut(df, args)
    elif args.course_grouping:
        run_course_grouping(df, args)
    else:
        run_single(df, args)


if __name__ == "__main__":
    main()
