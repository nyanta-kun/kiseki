"""JRA 乗数方式バックテスト

現行v17（加重合算）と提案乗数方式を比較検証する。

現行v17設計:
  composite = Σ(w_i × index_i)  ← 全指数を加重合算
  主な重み: pedigree=20.2%, last3f=13.7%, course=10.4%, jockey=10.0%, speed=9.3%

乗数方式設計:
  ability_base = weighted_avg(speed, last3f, course_aptitude, pedigree)
  K_jockey     = clip(1 + (jockey - 50)/50 × max_jk, ±max_jk)
  K_rotation   = clip(1 + (rotation - 50)/50 × 0.08, ±0.08)
  K_pace       = clip(1 + (pace - 50)/50 × 0.05, ±0.05)
  K_training   = clip(1 + (training - 50)/50 × 0.05, ±0.05)
  K_position   = clip(1 + (position - 50)/50 × 0.03, ±0.03)
  composite    = clip(ability_base × K_jockey × K_rotation × K_pace × K_training × K_position, 0, 100)

設計原則（地方v7と同様）:
  - 馬のタイム系/適性系能力がベース
  - 騎手・ローテ等は小幅補正のみ（大幅な順位変動を許さない）

使い方:
  cd backend
  uv run python scripts/jra_backtest_multiplier.py
  uv run python scripts/jra_backtest_multiplier.py --start 20250101 --end 20260415
  uv run python scripts/jra_backtest_multiplier.py --grid-search
  uv run python scripts/jra_backtest_multiplier.py --grid-search --top 20
  uv run python scripts/jra_backtest_multiplier.py --odds-cut
  uv run python scripts/jra_backtest_multiplier.py --by-surface
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
from src.indices.composite import COMPOSITE_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_backtest_multiplier")

engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# JRAコースコード（地方除外）
JRA_COURSE_CODES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

# グリッドサーチ対象: ability_base の内部重み
# (w_speed, w_last3f, w_course_aptitude, w_pedigree) — 正規化は自動
ABILITY_PARAM_SETS: list[tuple[float, float, float, float]] = [
    # speed / last3f / course_apt / pedigree
    (0.25, 0.25, 0.25, 0.25),  # 均等
    (0.30, 0.25, 0.25, 0.20),  # speed重視
    (0.20, 0.25, 0.25, 0.30),  # pedigree重視（v17的）
    (0.25, 0.30, 0.20, 0.25),  # last3f重視
    (0.25, 0.25, 0.35, 0.15),  # course_aptitude重視
    (0.35, 0.30, 0.20, 0.15),  # タイム系重視
    (0.20, 0.20, 0.30, 0.30),  # 適性・血統重視
    (0.30, 0.20, 0.30, 0.20),  # speed+course
    (0.40, 0.30, 0.15, 0.15),  # speed最重視
    (0.15, 0.25, 0.30, 0.30),  # pedigree+course
]

# K_jockey の最大効果（±X%）— グリッドサーチ対象
JOCKEY_MAX_VALS = [0.05, 0.08, 0.10, 0.12, 0.15]

# その他乗数（固定）
ROTATION_MAX  = 0.08
PACE_MAX      = 0.05
TRAINING_MAX  = 0.05
POSITION_MAX  = 0.03

# デフォルト（非グリッドサーチ時）
DEFAULT_ABILITY = (0.25, 0.25, 0.25, 0.25)
DEFAULT_JOCKEY_MAX = 0.10


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def load_data(start_date: str, end_date: str, version: int = COMPOSITE_VERSION) -> pd.DataFrame:
    """v17算出済み指数 + 実際の結果を一括取得する。"""
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    course_in = ", ".join(f"'{c}'" for c in JRA_COURSE_CODES)
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
    ci.last_3f_index      AS last3f_index,
    ci.course_aptitude    AS course_aptitude,
    ci.jockey_index       AS jockey_index,
    ci.rotation_index     AS rotation_index,
    ci.pedigree_index     AS pedigree_index,
    ci.pace_index         AS pace_index,
    ci.training_index     AS training_index,
    ci.position_advantage AS position_advantage,
    ci.composite_index    AS composite_v17,
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds,
    rr.win_popularity     AS win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :sd AND :ed
  AND ci.version = {version}
  AND r.course IN ({course_in})
ORDER BY r.date, r.id, ci.horse_id
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
        "speed_index", "last3f_index", "course_aptitude",
        "jockey_index", "rotation_index", "pedigree_index",
        "pace_index", "training_index", "position_advantage",
        "composite_v17", "finish_position", "win_odds",
        "win_popularity", "head_count", "distance", "abnormality_code",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    logger.info("取得: %d 件 / %d レース", len(df), df["race_id"].nunique())
    return df


# ---------------------------------------------------------------------------
# 乗数方式 composite 計算
# ---------------------------------------------------------------------------

def compute_multiplier(
    df: pd.DataFrame,
    w_speed: float,
    w_last3f: float,
    w_course: float,
    w_pedigree: float,
    max_jockey: float,
    max_rotation: float = ROTATION_MAX,
    max_pace: float = PACE_MAX,
    max_training: float = TRAINING_MAX,
    max_position: float = POSITION_MAX,
) -> pd.Series:
    """乗数方式で composite_index を計算して返す。

    ability_base = weighted_avg(speed, last3f, course_aptitude, pedigree)
    K_jockey   = clip(1 + (jockey - 50)/50 × max_jockey, ...)
    K_rotation = clip(1 + (rotation - 50)/50 × max_rotation, ...)
    K_pace     = clip(1 + (pace - 50)/50 × max_pace, ...)
    K_training = clip(1 + (training - 50)/50 × max_training, ...)
    K_position = clip(1 + (position - 50)/50 × max_position, ...)
    composite  = clip(ability_base × K_jockey × K_rotation × K_pace × K_training × K_position, 0, 100)
    """
    sp   = df["speed_index"].fillna(50.0)
    l3   = df["last3f_index"].fillna(50.0)
    ca   = df["course_aptitude"].fillna(50.0)
    ped  = df["pedigree_index"].fillna(50.0)
    jk   = df["jockey_index"].fillna(50.0)
    ro   = df["rotation_index"].fillna(50.0)
    pac  = df["pace_index"].fillna(50.0)
    tr   = df["training_index"].fillna(50.0)
    pos  = df["position_advantage"].fillna(50.0)

    # ability_base（正規化）
    total = w_speed + w_last3f + w_course + w_pedigree
    ability = (w_speed * sp + w_last3f * l3 + w_course * ca + w_pedigree * ped) / total

    def make_k(col: pd.Series, max_eff: float) -> pd.Series:
        k = 1.0 + (col - 50.0) / 50.0 * max_eff
        return k.clip(lower=1.0 - max_eff, upper=1.0 + max_eff)

    k_jockey   = make_k(jk,  max_jockey)
    k_rotation = make_k(ro,  max_rotation)
    k_pace     = make_k(pac, max_pace)
    k_training = make_k(tr,  max_training)
    k_position = make_k(pos, max_position)

    composite = (
        ability * k_jockey * k_rotation * k_pace * k_training * k_position
    ).clip(lower=0.0, upper=100.0)
    return composite


# ---------------------------------------------------------------------------
# データ前処理
# ---------------------------------------------------------------------------

def filter_valid(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """評価に使えないレース・馬を除外する。"""
    df = df[df["abnormality_code"] == 0].copy()
    df = df[df["finish_position"].notna()].copy()
    valid_races = df.groupby("race_id")["horse_id"].count()
    valid_races = valid_races[valid_races >= min_runners].index
    df = df[df["race_id"].isin(valid_races)].copy()
    return df


def add_ranks(df: pd.DataFrame, score_col: str, rank_col: str = "pred_rank") -> pd.DataFrame:
    df = df.copy()
    df[rank_col] = df.groupby("race_id")[score_col].rank(method="dense", ascending=False)
    return df


# ---------------------------------------------------------------------------
# 評価指標計算
# ---------------------------------------------------------------------------

def evaluate(df: pd.DataFrame, score_col: str, label: str = "") -> dict:
    """composite スコア列を使って各種指標を計算する。"""
    df = add_ranks(df, score_col, "pred_rank")
    top1 = df[df["pred_rank"] == 1].copy()

    total_races = top1["race_id"].nunique()
    win_cnt   = (top1["finish_position"] == 1).sum()
    place_cnt = (top1["finish_position"] <= 3).sum()
    win_rate   = win_cnt / total_races if total_races > 0 else 0.0
    place_rate = place_cnt / total_races if total_races > 0 else 0.0

    valid_odds = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    bets   = len(valid_odds)
    payout = valid_odds.loc[valid_odds["finish_position"] == 1, "win_odds"].sum()
    roi_win = float(payout / bets * 100) if bets > 0 else 0.0

    # 穴馬（単勝10倍以上）ROI
    upset = valid_odds[valid_odds["win_odds"] >= 10.0]
    bets_up   = len(upset)
    payout_up = upset.loc[upset["finish_position"] == 1, "win_odds"].sum()
    roi_upset = float(payout_up / bets_up * 100) if bets_up > 0 else 0.0

    # スピアマン順位相関
    corrs = []
    for _, grp in df.groupby("race_id"):
        if len(grp) < 4:
            continue
        rho, _ = spearmanr(grp["pred_rank"], grp["finish_position"])
        if not np.isnan(rho):
            corrs.append(rho)
    spearman = float(np.mean(corrs)) if corrs else 0.0

    return {
        "label":       label,
        "races":       int(total_races),
        "win_rate":    round(win_rate * 100, 2),
        "place_rate":  round(place_rate * 100, 2),
        "roi_win":     round(roi_win, 1),
        "roi_upset":   round(roi_upset, 1),
        "spearman":    round(spearman, 4),
        "upset_bets":  int(bets_up),
    }


def evaluate_by_surface(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """馬場面×距離区分別のROI・勝率を計算する。"""
    df = add_ranks(df, score_col, "pred_rank")
    top1 = df[df["pred_rank"] == 1].copy()

    def seg_label(row: pd.Series) -> str:
        surf = str(row["surface"]) if pd.notna(row["surface"]) else "不明"
        dist = row["distance"]
        if pd.isna(dist):
            return f"{surf}/不明"
        if dist <= 1400:
            return f"{surf}/スプリント(〜1400)"
        elif dist <= 1800:
            return f"{surf}/マイル(1401〜1800)"
        elif dist <= 2200:
            return f"{surf}/中距離(1801〜2200)"
        else:
            return f"{surf}/長距離(2201〜)"

    top1 = top1.copy()
    top1["segment"] = top1.apply(seg_label, axis=1)

    rows = []
    for seg, grp in top1.groupby("segment"):
        valid = grp[grp["win_odds"].notna() & (grp["win_odds"] > 0)]
        bets   = len(valid)
        payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
        roi    = float(payout / bets * 100) if bets > 0 else 0.0
        win_r  = (grp["finish_position"] == 1).mean() * 100
        rows.append({
            "segment":    seg,
            "races":      grp["race_id"].nunique(),
            "win_rate":   round(win_r, 2),
            "roi_win":    round(roi, 1),
        })
    return pd.DataFrame(rows).sort_values("races", ascending=False)


def evaluate_by_course(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """競馬場別のROI・勝率を計算する。"""
    df = add_ranks(df, score_col, "pred_rank")
    top1 = df[df["pred_rank"] == 1].copy()

    rows = []
    for course, grp in top1.groupby("course_name"):
        valid = grp[grp["win_odds"].notna() & (grp["win_odds"] > 0)]
        bets   = len(valid)
        payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
        roi    = float(payout / bets * 100) if bets > 0 else 0.0
        win_r  = (grp["finish_position"] == 1).mean() * 100
        rows.append({
            "course_name": course,
            "races":       grp["race_id"].nunique(),
            "win_rate":    round(win_r, 2),
            "roi_win":     round(roi, 1),
        })
    return pd.DataFrame(rows).sort_values("races", ascending=False)


def evaluate_odds_cut(
    df: pd.DataFrame,
    score_col: str,
    thresholds: list[float] | None = None,
) -> pd.DataFrame:
    """指数1位馬の低オッズカット別ROIを計算する。

    低オッズの人気馬（確実性が高い馬）を除いた場合のROIを計算。
    """
    if thresholds is None:
        thresholds = [0.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0]

    df = add_ranks(df, score_col, "pred_rank")
    top1 = df[df["pred_rank"] == 1].copy()
    valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]

    rows = []
    for thr in thresholds:
        subset = valid[valid["win_odds"] >= thr] if thr > 0 else valid
        bets   = len(subset)
        payout = subset.loc[subset["finish_position"] == 1, "win_odds"].sum()
        roi    = float(payout / bets * 100) if bets > 0 else 0.0
        rows.append({
            "odds_cut": f">= {thr:.1f}倍" if thr > 0 else "全て",
            "bets":     bets,
            "roi_win":  round(roi, 1),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# グリッドサーチ
# ---------------------------------------------------------------------------

def run_grid_search(df: pd.DataFrame, top_n: int = 20) -> None:
    """ability_base重み × max_jockey の全組み合わせをグリッドサーチ。"""
    logger.info("グリッドサーチ開始: %d ability sets × %d jockey vals = %d 組",
                len(ABILITY_PARAM_SETS), len(JOCKEY_MAX_VALS),
                len(ABILITY_PARAM_SETS) * len(JOCKEY_MAX_VALS))

    v17_metrics = evaluate(df, "composite_v17", "v17_baseline")
    print(f"\n[ベースライン] v17: ROI={v17_metrics['roi_win']}%, "
          f"穴馬ROI={v17_metrics['roi_upset']}%, "
          f"勝率={v17_metrics['win_rate']}%, "
          f"Spearman={v17_metrics['spearman']}, "
          f"レース数={v17_metrics['races']}")

    results = []
    total = len(ABILITY_PARAM_SETS) * len(JOCKEY_MAX_VALS)
    for i, ((ws, wl, wc, wp), max_jk) in enumerate(
        itertools.product(ABILITY_PARAM_SETS, JOCKEY_MAX_VALS), 1
    ):
        df["composite_new"] = compute_multiplier(
            df, ws, wl, wc, wp, max_jk
        )
        m = evaluate(df, "composite_new")
        results.append({
            "w_speed":   ws,
            "w_last3f":  wl,
            "w_course":  wc,
            "w_pedigree": wp,
            "max_jockey": max_jk,
            "roi_win":   m["roi_win"],
            "roi_upset": m["roi_upset"],
            "win_rate":  m["win_rate"],
            "spearman":  m["spearman"],
            "diff_roi":      round(m["roi_win"] - v17_metrics["roi_win"], 2),
            "diff_roi_upset": round(m["roi_upset"] - v17_metrics["roi_upset"], 2),
        })
        if i % 10 == 0:
            logger.info("  %d/%d 完了", i, total)

    res_df = pd.DataFrame(results)

    # ROI基準でソート
    print(f"\n=== グリッドサーチ結果 TOP{top_n} (単勝ROI順) ===")
    top = res_df.sort_values("roi_win", ascending=False).head(top_n)
    print(top.to_string(index=False))

    # 穴馬ROI基準でもトップ表示
    print(f"\n=== グリッドサーチ結果 TOP{top_n} (穴馬ROI順) ===")
    top_u = res_df.sort_values("roi_upset", ascending=False).head(top_n)
    print(top_u.to_string(index=False))

    # v17より全体ROI・穴馬ROI両方改善したパターン
    both_better = res_df[(res_df["diff_roi"] > 0) & (res_df["diff_roi_upset"] > 0)]
    print(f"\n=== v17より全体ROI・穴馬ROI両方改善: {len(both_better)}件 ===")
    if len(both_better) > 0:
        print(both_better.sort_values("diff_roi_upset", ascending=False).head(20).to_string(index=False))

    # 最良パラメータ
    best = res_df.sort_values("roi_win", ascending=False).iloc[0]
    print(f"\n=== 全体ROI最良パラメータ ===")
    print(f"  speed={best.w_speed}, last3f={best.w_last3f}, course={best.w_course}, pedigree={best.w_pedigree}")
    print(f"  max_jockey={best.max_jockey}")
    print(f"  ROI: {best.roi_win}% (v17比 {best.diff_roi:+.2f}%)")
    print(f"  穴馬ROI: {best.roi_upset}% (v17比 {best.diff_roi_upset:+.2f}%)")

    best_u = res_df.sort_values("roi_upset", ascending=False).iloc[0]
    print(f"\n=== 穴馬ROI最良パラメータ ===")
    print(f"  speed={best_u.w_speed}, last3f={best_u.w_last3f}, course={best_u.w_course}, pedigree={best_u.w_pedigree}")
    print(f"  max_jockey={best_u.max_jockey}")
    print(f"  ROI: {best_u.roi_win}% (v17比 {best_u.diff_roi:+.2f}%)")
    print(f"  穴馬ROI: {best_u.roi_upset}% (v17比 {best_u.diff_roi_upset:+.2f}%)")


# ---------------------------------------------------------------------------
# メイン比較
# ---------------------------------------------------------------------------

def run_comparison(df: pd.DataFrame, w_speed: float, w_last3f: float,
                   w_course: float, w_pedigree: float, max_jockey: float) -> None:
    """v17 vs 乗数方式の詳細比較。"""
    df["composite_new"] = compute_multiplier(
        df, w_speed, w_last3f, w_course, w_pedigree, max_jockey
    )

    v17 = evaluate(df, "composite_v17", "v17（加重合算）")
    new = evaluate(df, "composite_new", f"乗数方式(sp={w_speed},l3={w_last3f},ca={w_course},ped={w_pedigree},jk±{max_jockey*100:.0f}%)")

    print("\n" + "=" * 70)
    print("=== v17（加重合算）vs 乗数方式 比較 ===")
    print("=" * 70)
    hdrs = ["指標", "v17", "乗数方式", "差"]
    rows = [
        ["レース数",    str(v17["races"]),             str(new["races"]),             "-"],
        ["勝率",        f"{v17['win_rate']}%",         f"{new['win_rate']}%",         f"{new['win_rate']-v17['win_rate']:+.2f}%"],
        ["3着内率",     f"{v17['place_rate']}%",       f"{new['place_rate']}%",       f"{new['place_rate']-v17['place_rate']:+.2f}%"],
        ["単勝ROI",     f"{v17['roi_win']}%",          f"{new['roi_win']}%",          f"{new['roi_win']-v17['roi_win']:+.1f}%"],
        ["穴馬ROI(10倍以上)", f"{v17['roi_upset']}%", f"{new['roi_upset']}%",        f"{new['roi_upset']-v17['roi_upset']:+.1f}%"],
        ["穴馬ベット数", str(v17["upset_bets"]),       str(new["upset_bets"]),        "-"],
        ["Spearman",   str(v17["spearman"]),           str(new["spearman"]),          f"{new['spearman']-v17['spearman']:+.4f}"],
    ]
    col_w = [20, 15, 30, 10]
    header = "".join(f"{h:<{w}}" for h, w in zip(hdrs, col_w))
    print(header)
    print("-" * sum(col_w))
    for r in rows:
        print("".join(f"{c:<{w}}" for c, w in zip(r, col_w)))


def run_odds_cut(df: pd.DataFrame, w_speed: float, w_last3f: float,
                 w_course: float, w_pedigree: float, max_jockey: float) -> None:
    """低オッズカット別ROI比較。"""
    df["composite_new"] = compute_multiplier(
        df, w_speed, w_last3f, w_course, w_pedigree, max_jockey
    )
    thr_list = [0.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0]

    cuts_v17 = evaluate_odds_cut(df, "composite_v17", thr_list)
    cuts_new = evaluate_odds_cut(df, "composite_new",  thr_list)

    print("\n=== オッズカット別ROI比較 ===")
    merged = cuts_v17.rename(columns={"bets": "bets_v17", "roi_win": "roi_v17"}).merge(
        cuts_new.rename(columns={"bets": "bets_new", "roi_win": "roi_new"}),
        on="odds_cut",
    )
    merged["diff"] = (merged["roi_new"] - merged["roi_v17"]).round(1)
    print(merged.to_string(index=False))


def run_by_surface(df: pd.DataFrame, w_speed: float, w_last3f: float,
                   w_course: float, w_pedigree: float, max_jockey: float) -> None:
    """馬場面×距離セグメント別比較。"""
    df["composite_new"] = compute_multiplier(
        df, w_speed, w_last3f, w_course, w_pedigree, max_jockey
    )
    surf_v17 = evaluate_by_surface(df, "composite_v17").rename(
        columns={"win_rate": "win_v17", "roi_win": "roi_v17"}
    )
    surf_new = evaluate_by_surface(df, "composite_new").rename(
        columns={"win_rate": "win_new", "roi_win": "roi_new"}
    )
    merged = surf_v17.merge(surf_new.drop(columns=["races"]), on="segment", how="outer")
    merged["diff_roi"] = (merged["roi_new"] - merged["roi_v17"]).round(1)
    merged = merged.sort_values("races", ascending=False)

    print("\n=== 馬場面×距離別ROI比較 ===")
    print(merged.to_string(index=False))


def run_by_course(df: pd.DataFrame, w_speed: float, w_last3f: float,
                  w_course: float, w_pedigree: float, max_jockey: float) -> None:
    """競馬場別ROI比較。"""
    df["composite_new"] = compute_multiplier(
        df, w_speed, w_last3f, w_course, w_pedigree, max_jockey
    )
    c_v17 = evaluate_by_course(df, "composite_v17").rename(
        columns={"win_rate": "win_v17", "roi_win": "roi_v17"}
    )
    c_new = evaluate_by_course(df, "composite_new").rename(
        columns={"win_rate": "win_new", "roi_win": "roi_new"}
    )
    merged = c_v17.merge(c_new.drop(columns=["races"]), on="course_name", how="outer")
    merged["diff_roi"] = (merged["roi_new"] - merged["roi_v17"]).round(1)
    merged = merged.sort_values("races", ascending=False)

    print("\n=== 競馬場別ROI比較 ===")
    print(merged.to_string(index=False))


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:  # noqa: PLR0912
    parser = argparse.ArgumentParser(description="JRA乗数方式バックテスト")
    parser.add_argument("--start", default="20250101", help="開始日 YYYYMMDD")
    parser.add_argument("--end",   default="20260415", help="終了日 YYYYMMDD")
    parser.add_argument("--version", type=int, default=COMPOSITE_VERSION,
                        help=f"使用するDB版本（デフォルト: {COMPOSITE_VERSION}）")
    parser.add_argument("--grid-search", action="store_true",
                        help="ability重み×max_jockeyのグリッドサーチを実行")
    parser.add_argument("--top", type=int, default=20,
                        help="グリッドサーチ結果の表示件数")
    parser.add_argument("--odds-cut", action="store_true",
                        help="低オッズカット別ROI比較を実行")
    parser.add_argument("--by-surface", action="store_true",
                        help="馬場面×距離セグメント別ROI比較を実行")
    parser.add_argument("--by-course", action="store_true",
                        help="競馬場別ROI比較を実行")
    # カスタムパラメータ
    parser.add_argument("--w-speed",   type=float, default=DEFAULT_ABILITY[0])
    parser.add_argument("--w-last3f",  type=float, default=DEFAULT_ABILITY[1])
    parser.add_argument("--w-course",  type=float, default=DEFAULT_ABILITY[2])
    parser.add_argument("--w-pedigree", type=float, default=DEFAULT_ABILITY[3])
    parser.add_argument("--max-jockey", type=float, default=DEFAULT_JOCKEY_MAX)
    args = parser.parse_args()

    logger.info("データ取得: %s〜%s (version=%d)", args.start, args.end, args.version)
    df_raw = load_data(args.start, args.end, args.version)
    if df_raw.empty:
        logger.error("データが取得できませんでした")
        return

    df = filter_valid(df_raw)
    logger.info("有効レース: %d / 全馬: %d", df["race_id"].nunique(), len(df))

    ws, wl, wc, wp = args.w_speed, args.w_last3f, args.w_course, args.w_pedigree
    max_jk = args.max_jockey

    if args.grid_search:
        run_grid_search(df, top_n=args.top)
    else:
        run_comparison(df, ws, wl, wc, wp, max_jk)

    if args.odds_cut:
        run_odds_cut(df, ws, wl, wc, wp, max_jk)

    if args.by_surface:
        run_by_surface(df, ws, wl, wc, wp, max_jk)

    if args.by_course:
        run_by_course(df, ws, wl, wc, wp, max_jk)


if __name__ == "__main__":
    main()
