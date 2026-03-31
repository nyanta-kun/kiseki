"""EV分析・悪条件検出・回収率改善検証スクリプト

以下を一括実行する:
  1. EV分析: 指数から推定勝率を算出し期待値(EV)の有効性を検証
  2. 悪条件検出: 回収率が低い軸（芝/ダート/距離/頭数/グレード/競馬場/条件クラス等）を特定
  3. フィルター効果: 悪条件を除外した場合のROI改善を定量化
  4. 重み評価: 各指数のスピアマン相関を条件別に集計

使い方:
  uv run python scripts/ev_analysis.py --start 20240101 --end 20261231
  uv run python scripts/ev_analysis.py --start 20250101 --end 20261231  # odds充実期間に絞る
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

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
logger = logging.getLogger("ev_analysis")

RACE_TYPE_LABEL = {
    "00": "新馬",
    "11": "未勝利",
    "12": "1勝クラス",
    "13": "2勝クラス",
    "14": "3勝クラス",
    "19": "オープン",
    None: "一般",
}

INDEX_COLS = [
    "composite_index",
    "speed_index",
    "last_3f_index",
    "course_aptitude",
    "position_advantage",
    "jockey_index",
    "pace_index",
    "rotation_index",
    "pedigree_index",
    "training_index",
]
INDEX_LABELS = {
    "composite_index": "総合",
    "speed_index": "スピード",
    "last_3f_index": "後3F",
    "course_aptitude": "コース適性",
    "position_advantage": "枠順",
    "jockey_index": "騎手",
    "pace_index": "展開",
    "rotation_index": "ローテ",
    "pedigree_index": "血統",
    "training_index": "調教",
}


# ============================================================
# データ取得
# ============================================================


def load_data(start_date: str, end_date: str, version: int = COMPOSITE_VERSION) -> pd.DataFrame:
    """算出指数・レース結果・オッズを結合取得する。"""
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

    sql = text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course          AS course,
    r.course_name     AS course_name,
    r.grade           AS grade,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    r.race_type_code  AS race_type_code,
    r.condition       AS condition,
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
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds,
    rr.win_popularity     AS win_popularity,
    rr.horse_number       AS horse_number
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :sd AND :ed
  AND ci.version = {version}
  AND r.course_name ~ '^[^\x30-\x39]'
ORDER BY r.date, r.id, ci.horse_id
""")
    with Session(engine) as db:
        result = db.execute(sql, {"sd": sd, "ed": ed})
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=cols)
    for col in INDEX_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")

    logger.info(f"取得: {len(df):,}件 / {df['race_id'].nunique():,}レース")
    return df


def filter_valid(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """異常コード・着順NULL・指数NULL・少頭数レースを除外する。"""
    bad = df[
        (df["abnormality_code"] > 0) | df["finish_position"].isna() | df["composite_index"].isna()
    ]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()
    cnts = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(cnts[cnts >= min_runners].index)].copy()
    logger.info(f"フィルタ後: {len(df):,}件 / {df['race_id'].nunique():,}レース")
    return df


# ============================================================
# ユーティリティ
# ============================================================


def softmax_probs(scores: np.ndarray, alpha: float = 0.15) -> np.ndarray:
    """スコアをソフトマックスで確率化する（alpha=温度パラメータ）。"""
    s = alpha * (scores - scores.mean())
    e = np.exp(s - s.max())
    return e / e.sum()


def calc_ev_per_race(df: pd.DataFrame, alpha: float = 0.15) -> pd.DataFrame:
    """レースごとにEV（推定勝率×オッズ）を算出して列を追加する。"""
    rows = []
    for race_id, grp in df.groupby("race_id"):
        scores = grp["composite_index"].to_numpy(dtype=float)
        probs = softmax_probs(scores, alpha)
        for i, (idx, row) in enumerate(grp.iterrows()):
            rows.append({"race_id": race_id, "_idx": idx, "est_prob": probs[i]})
    prob_df = pd.DataFrame(rows).set_index("_idx")
    df = df.copy()
    df["est_prob"] = prob_df["est_prob"]
    df["ev"] = df["est_prob"] * df["win_odds"]
    return df


def roi_stats(top1: pd.DataFrame) -> dict:
    """単勝top1のROI統計を返す。"""
    valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)].copy()
    if len(valid) == 0:
        return {"bets": 0, "wins": 0, "win_rate": 0.0, "roi_pct": 0.0}
    wins = (valid["finish_position"] == 1).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    roi = payout / len(valid) * 100
    return {
        "bets": len(valid),
        "wins": int(wins),
        "win_rate": round(wins / len(valid) * 100, 1),
        "roi_pct": round(roi, 1),
    }


def top1_of(df: pd.DataFrame) -> pd.DataFrame:
    """各レースの指数top1馬を返す。"""
    return df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()


def spearman_mean(df: pd.DataFrame, col: str) -> float:
    """レースごとスピアマン相関の平均を返す。"""
    rhos = []
    for _, grp in df.groupby("race_id"):
        if len(grp) < 3 or grp[col].isna().any():
            continue
        rho, _ = stats.spearmanr(grp[col], -grp["finish_position"])
        if not np.isnan(rho):
            rhos.append(rho)
    return float(np.mean(rhos)) if rhos else 0.0


SURFACE_LABEL = {"芝": "芝", "ダ": "ダート", "障": "障害"}


def distance_cat(d: float) -> str:
    if d <= 1400:
        return "スプリント(〜1400)"
    if d <= 1800:
        return "マイル(1401-1800)"
    if d <= 2400:
        return "中距離(1801-2400)"
    return "長距離(2401〜)"


def head_count_cat(h: float) -> str:
    if h <= 8:
        return "少頭数(〜8)"
    if h <= 13:
        return "中頭数(9-13)"
    return "多頭数(14〜)"


def grade_cat(g, rtc) -> str:
    if g in ("G1", "G2", "G3", "J.G1", "J.G2", "J.G3", "Listed"):
        return g or "重賞"
    if g == "OP特別":
        return "OP特別"
    rtc = str(rtc) if rtc else None
    return RACE_TYPE_LABEL.get(rtc, "一般")


def popularity_cat(p: float) -> str:
    if p <= 3:
        return "1〜3番人気"
    if p <= 6:
        return "4〜6番人気"
    if p <= 9:
        return "7〜9番人気"
    return "10番人気以下"


# ============================================================
# Section 1: EV分析
# ============================================================


def section_ev(df: pd.DataFrame) -> None:
    print("\n" + "=" * 65)
    print("【1】EV（期待値）分析")
    print("=" * 65)

    df = calc_ev_per_race(df)
    top1 = top1_of(df)
    s = roi_stats(top1)
    print("\n▼ 全体（指数top1 単勝）")
    print(
        f"  賭け数: {s['bets']:,}, 的中: {s['wins']}, 勝率: {s['win_rate']}%, ROI: {s['roi_pct']}%"
    )

    # EV閾値別
    print("\n▼ EV閾値別 ROI（指数top1かつEV≥X の馬のみ賭ける）")
    print(f"  {'EV閾値':<10} {'賭け数':>7} {'的中':>6} {'勝率':>7} {'ROI':>8}")
    print("  " + "-" * 45)
    valid_odds = df[df["win_odds"].notna() & df["ev"].notna()]
    for threshold in [0.5, 0.7, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 2.0]:
        # 各レースでtop1かつEV≥閾値の馬
        sub = valid_odds[valid_odds["ev"] >= threshold]
        sub_top1 = (
            sub.loc[sub.groupby("race_id")["composite_index"].idxmax()]
            if len(sub) > 0
            else pd.DataFrame()
        )
        if len(sub_top1) == 0:
            continue
        s2 = roi_stats(sub_top1)
        print(
            f"  EV≥{threshold:<6.1f}  {s2['bets']:>7,}  {s2['wins']:>6}  {s2['win_rate']:>6.1f}%  {s2['roi_pct']:>7.1f}%"
        )

    # ソフトマックスのalphaチューニング
    print("\n▼ 推定確率の温度パラメータ(alpha)チューニング")
    print(f"  {'alpha':<8} {'EV≥1.0賭け数':>12} {'ROI':>8}")
    print("  " + "-" * 35)
    valid_with_odds = df[df["win_odds"].notna() & (df["win_odds"] > 0)].copy()
    for alpha in [0.05, 0.10, 0.15, 0.20, 0.30]:
        rows = []
        for race_id, grp in valid_with_odds.groupby("race_id"):
            scores = grp["composite_index"].to_numpy(float)
            probs = softmax_probs(scores, alpha)
            for i, (_, row) in enumerate(grp.iterrows()):
                ev_val = probs[i] * row["win_odds"]
                rows.append(
                    {
                        "race_id": race_id,
                        "ev": ev_val,
                        "score": row["composite_index"],
                        "finish_position": row["finish_position"],
                        "win_odds": row["win_odds"],
                    }
                )
        tdf = pd.DataFrame(rows)
        sub = tdf[tdf["ev"] >= 1.0]
        sub_top1 = (
            sub.loc[sub.groupby("race_id")["score"].idxmax()] if len(sub) > 0 else pd.DataFrame()
        )
        if len(sub_top1) == 0:
            continue
        s3 = roi_stats(
            sub_top1.rename(columns={"finish_position": "finish_position", "win_odds": "win_odds"})
        )
        print(f"  alpha={alpha:<5.2f}  {s3['bets']:>12,}  {s3['roi_pct']:>7.1f}%")


# ============================================================
# Section 2: 各指数のスピアマン相関
# ============================================================


def section_index_power(df: pd.DataFrame) -> None:
    print("\n" + "=" * 65)
    print("【2】各指数の予測力（スピアマン相関）")
    print("=" * 65)

    print(f"\n  {'指数':<15} {'全体ρ':>8} {'芝ρ':>8} {'ダートρ':>8} {'2024ρ':>8} {'2025〜ρ':>8}")
    print("  " + "-" * 60)

    df_turf = df[df["surface"] == "芝"]
    df_dirt = df[df["surface"] == "ダ"]
    df_2024 = df[df["date"].astype(str) < "20250101"]
    df_2025 = df[df["date"].astype(str) >= "20250101"]

    for col in INDEX_COLS:
        if df[col].isna().all():
            continue
        lbl = INDEX_LABELS.get(col, col)
        rho_all = spearman_mean(df, col)
        rho_turf = spearman_mean(df_turf, col) if len(df_turf) > 0 else float("nan")
        rho_dirt = spearman_mean(df_dirt, col) if len(df_dirt) > 0 else float("nan")
        rho_2024 = spearman_mean(df_2024, col) if len(df_2024) > 0 else float("nan")
        rho_2025 = spearman_mean(df_2025, col) if len(df_2025) > 0 else float("nan")
        print(
            f"  {lbl:<15} {rho_all:>+8.4f} {rho_turf:>+8.4f} {rho_dirt:>+8.4f} {rho_2024:>+8.4f} {rho_2025:>+8.4f}"
        )


# ============================================================
# Section 3: 悪条件検出
# ============================================================


def _breakdown_roi(df: pd.DataFrame, key_col: str, label: str) -> pd.DataFrame:
    """指定軸ごとに指数top1の単勝ROIを集計する。"""
    rows = []
    for key, grp in df.groupby(key_col):
        t1 = grp.loc[grp.groupby("race_id")["composite_index"].idxmax()]
        s = roi_stats(t1)
        avg_runners = grp.groupby("race_id")["horse_id"].count().mean()
        rho = spearman_mean(grp, "composite_index")
        rows.append(
            {
                label: key,
                "レース数": grp["race_id"].nunique(),
                "的中": s["wins"],
                "賭け数": s["bets"],
                "勝率%": s["win_rate"],
                "ROI%": s["roi_pct"],
                "ランダム勝率%": round(100.0 / avg_runners, 1) if avg_runners > 0 else 0.0,
                "スピアマンρ": round(rho, 4),
            }
        )
    return pd.DataFrame(rows)


def section_bad_conditions(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """悪条件を検出し、各軸のROI表を返す。"""
    print("\n" + "=" * 65)
    print("【3】悪条件検出（ROIが低い軸の特定）")
    print("=" * 65)

    # 軸を追加
    df = df.copy()
    df["surface_label"] = df["surface"].map(SURFACE_LABEL).fillna(df["surface"])
    df["distance_cat"] = df["distance"].apply(distance_cat)
    df["head_count_cat"] = df["head_count"].apply(head_count_cat)
    df["grade_cat"] = df.apply(lambda r: grade_cat(r["grade"], r["race_type_code"]), axis=1)
    df["pop_cat"] = df["win_popularity"].apply(
        lambda p: popularity_cat(p) if pd.notna(p) else "不明"
    )

    def print_table(tbl: pd.DataFrame, col: str) -> None:
        for _, row in tbl.sort_values("ROI%").iterrows():
            flag = " ⚠️ " if row["ROI%"] < 75 else ("  ✅" if row["ROI%"] > 100 else "    ")
            print(
                f"  {flag}{row[col]:<22} "
                f"R:{row['レース数']:>5} "
                f"勝率:{row['勝率%']:>5.1f}% "
                f"ROI:{row['ROI%']:>6.1f}% "
                f"(ランダム:{row['ランダム勝率%']:>4.1f}%) "
                f"ρ:{row['スピアマンρ']:>+.3f}"
            )

    results = {}

    print("\n▼ 芝/ダート別")
    tbl = _breakdown_roi(df, "surface_label", "surface_label")
    print_table(tbl, "surface_label")
    results["surface"] = tbl

    print("\n▼ 距離カテゴリ別")
    tbl = _breakdown_roi(df, "distance_cat", "distance_cat")
    print_table(tbl, "distance_cat")
    results["distance"] = tbl

    print("\n▼ 頭数別")
    tbl = _breakdown_roi(df, "head_count_cat", "head_count_cat")
    print_table(tbl, "head_count_cat")
    results["head_count"] = tbl

    print("\n▼ グレード・条件クラス別")
    tbl = _breakdown_roi(df, "grade_cat", "grade_cat")
    print_table(tbl, "grade_cat")
    results["grade"] = tbl

    print("\n▼ 競馬場別")
    tbl = _breakdown_roi(df, "course_name", "course_name")
    print_table(tbl, "course_name")
    results["course"] = tbl

    print("\n▼ 馬場状態別")
    tbl = _breakdown_roi(df, "condition", "condition")
    print_table(tbl, "condition")
    results["condition"] = tbl

    print("\n▼ 指数1-2位差（gap_12）別")
    # gap計算
    gap_rows = []
    for race_id, grp in df.groupby("race_id"):
        sg = grp.sort_values("composite_index", ascending=False).reset_index(drop=True)
        if len(sg) < 2:
            continue
        gap = float(sg.iloc[0]["composite_index"]) - float(sg.iloc[1]["composite_index"])
        grp = grp.copy()
        grp["gap_12"] = gap
        gap_rows.append(grp)
    df_gap = pd.concat(gap_rows) if gap_rows else df.copy()

    def gap_bucket(g):
        if g < 3:
            return "0〜3未満（拮抗）"
        if g < 6:
            return "3〜6未満"
        if g < 10:
            return "6〜10未満"
        if g < 15:
            return "10〜15未満"
        return "15以上（支配的）"

    df_gap["gap_cat"] = df_gap["gap_12"].apply(gap_bucket)
    tbl = _breakdown_roi(df_gap, "gap_cat", "gap_cat")
    print_table(tbl, "gap_cat")
    results["gap"] = tbl

    print("\n▼ 人気別（top1馬の人気）")
    # top1馬の人気をレースに付与
    pop_rows = []
    for race_id, grp in df.groupby("race_id"):
        top = grp.loc[grp["composite_index"].idxmax()]
        pop = top["win_popularity"]
        if pd.isna(pop):
            continue
        p_cat = popularity_cat(float(pop))
        grp = grp.copy()
        grp["top1_pop_cat"] = p_cat
        pop_rows.append(grp)
    df_pop = pd.concat(pop_rows) if pop_rows else df.copy()
    tbl = _breakdown_roi(df_pop, "top1_pop_cat", "top1_pop_cat")
    print_table(tbl, "top1_pop_cat")
    results["popularity"] = tbl

    return results


# ============================================================
# Section 4: フィルター効果
# ============================================================


def section_filter_effect(df: pd.DataFrame, bad_results: dict) -> None:
    """悪条件を除外した場合のROI改善を検証する。"""
    print("\n" + "=" * 65)
    print("【4】フィルター効果（悪条件除外後の回収率改善）")
    print("=" * 65)

    # ベースライン
    top1_base = top1_of(df)
    base = roi_stats(top1_base)
    n_base = df["race_id"].nunique()
    print(f"\n▼ ベースライン: {n_base:,}レース / ROI={base['roi_pct']}% / 勝率={base['win_rate']}%")

    # 各軸の閾値を自動検出（ROI < 75% を悪条件とする）
    df = df.copy()
    df["distance_cat"] = df["distance"].apply(distance_cat)
    df["head_count_cat"] = df["head_count"].apply(head_count_cat)
    df["grade_cat"] = df.apply(lambda r: grade_cat(r["grade"], r["race_type_code"]), axis=1)

    # gap再計算
    gap_map = {}
    for race_id, grp in df.groupby("race_id"):
        sg = grp.sort_values("composite_index", ascending=False).reset_index(drop=True)
        if len(sg) < 2:
            gap_map[race_id] = 0.0
        else:
            gap_map[race_id] = float(sg.iloc[0]["composite_index"]) - float(
                sg.iloc[1]["composite_index"]
            )
    df["gap_12"] = df["race_id"].map(gap_map)

    def extract_bad(tbl: pd.DataFrame, col: str, threshold: float = 75.0) -> list:
        return tbl.loc[tbl["ROI%"] < threshold, col].tolist()

    bad_surfaces = extract_bad(bad_results.get("surface", pd.DataFrame()), "surface_label")
    bad_distances = extract_bad(bad_results.get("distance", pd.DataFrame()), "distance_cat")
    bad_grades = extract_bad(bad_results.get("grade", pd.DataFrame()), "grade_cat")
    bad_conditions = extract_bad(bad_results.get("condition", pd.DataFrame()), "condition")
    bad_head_counts = extract_bad(bad_results.get("head_count", pd.DataFrame()), "head_count_cat")
    bad_courses = extract_bad(bad_results.get("course", pd.DataFrame()), "course_name")

    filters = [
        (
            "芝/ダート除外",
            "surface_label",
            lambda d: (
                ~d["surface"].isin([k for k, v in SURFACE_LABEL.items() if v in bad_surfaces])
            ),
        ),
        ("距離カテゴリ除外", "distance_cat", lambda d: ~d["distance_cat"].isin(bad_distances)),
        ("グレード除外", "grade_cat", lambda d: ~d["grade_cat"].isin(bad_grades)),
        ("馬場状態除外", "condition", lambda d: ~d["condition"].isin(bad_conditions)),
        ("頭数除外", "head_count_cat", lambda d: ~d["head_count_cat"].isin(bad_head_counts)),
        ("競馬場除外", "course_name", lambda d: ~d["course_name"].isin(bad_courses)),
        ("指数差<3(拮抗)除外", "gap_12", lambda d: d["gap_12"] >= 3),
    ]

    print(
        f"\n  {'フィルター':<25} {'除外条件':<35} {'残レース':>8} {'ROI%':>8} {'勝率%':>7} {'改善':>8}"
    )
    print("  " + "-" * 100)

    cumulative_mask = pd.Series(True, index=df.index)
    for fname, col, mask_fn in filters:
        # 個別フィルター
        if col in (
            "surface",
            "distance_cat",
            "grade_cat",
            "condition",
            "head_count_cat",
            "course_name",
        ):
            bad_list = {
                "surface": bad_surfaces,
                "distance_cat": bad_distances,
                "grade_cat": bad_grades,
                "condition": bad_conditions,
                "head_count_cat": bad_head_counts,
                "course_name": bad_courses,
            }.get(col, [])
            if not bad_list:
                continue
            bad_str = ", ".join(str(b) for b in bad_list[:3])
            if len(bad_list) > 3:
                bad_str += f"... (+{len(bad_list) - 3})"
        else:
            bad_str = "gap_12 < 3"

        filtered = df[mask_fn(df)]
        if filtered["race_id"].nunique() == 0:
            continue
        t1 = top1_of(filtered)
        s = roi_stats(t1)
        improve = s["roi_pct"] - base["roi_pct"]
        print(
            f"  {fname:<25} {bad_str:<35} {filtered['race_id'].nunique():>8,} "
            f"{s['roi_pct']:>7.1f}% {s['win_rate']:>6.1f}% "
            f"{'↑' if improve > 0 else '↓'}{abs(improve):>6.1f}%"
        )

        # 累積フィルター適用
        cumulative_mask &= mask_fn(df)

    # 全悪条件を重ねた場合
    df_cum = df[cumulative_mask]
    n_cum = df_cum["race_id"].nunique()
    if n_cum > 0:
        t1_cum = top1_of(df_cum)
        s_cum = roi_stats(t1_cum)
        improve_cum = s_cum["roi_pct"] - base["roi_pct"]
        print(
            f"\n  {'★ 全フィルター累積':<25} {'（上記すべて除外）':<35} {n_cum:>8,} "
            f"{s_cum['roi_pct']:>7.1f}% {s_cum['win_rate']:>6.1f}% "
            f"{'↑' if improve_cum > 0 else '↓'}{abs(improve_cum):>6.1f}%"
        )
        print(
            f"  （除外レース数: {n_base - n_cum:,} / {n_base:,} = {(n_base - n_cum) / n_base * 100:.0f}% を非賭け対象に）"
        )

    # EV閾値と悪条件の組み合わせ
    print("\n▼ EV≥X + 全フィルター累積 の組み合わせ")
    print(f"  {'EV閾値':<10} {'賭け数':>8} {'ROI%':>8} {'改善':>8}")
    print("  " + "-" * 40)
    df_cum_ev = calc_ev_per_race(df_cum) if n_cum > 0 else pd.DataFrame()
    for threshold in [0.8, 1.0, 1.1, 1.2, 1.5]:
        if df_cum_ev.empty:
            break
        sub = df_cum_ev[df_cum_ev["ev"] >= threshold]
        if len(sub) == 0:
            continue
        sub_t1 = sub.loc[sub.groupby("race_id")["composite_index"].idxmax()]
        s = roi_stats(sub_t1)
        improve = s["roi_pct"] - base["roi_pct"]
        print(
            f"  EV≥{threshold:<6.1f}  {s['bets']:>8,}  {s['roi_pct']:>7.1f}%  "
            f"{'↑' if improve > 0 else '↓'}{abs(improve):>6.1f}%"
        )


# ============================================================
# Section 5: 月別P&L推移
# ============================================================


def section_monthly_pnl(df: pd.DataFrame, df_filtered: pd.DataFrame | None = None) -> None:
    print("\n" + "=" * 65)
    print("【5】月別P&L推移（単勝100円均一）")
    print("=" * 65)

    for label, target in [("全条件", df), ("フィルター後", df_filtered)]:
        if target is None or target.empty:
            continue
        top1 = top1_of(target)
        valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)].copy()
        valid["ym"] = valid["date"].astype(str).str[:6]
        valid["profit"] = valid.apply(
            lambda r: r["win_odds"] * 100 - 100 if r["finish_position"] == 1 else -100, axis=1
        )
        monthly = (
            valid.groupby("ym")
            .agg(bets=("profit", "count"), profit=("profit", "sum"))
            .reset_index()
        )
        monthly["cumulative"] = monthly["profit"].cumsum()
        monthly["roi"] = (
            valid.groupby("ym")
            .apply(lambda g: g["win_odds"][g["finish_position"] == 1].sum() / len(g) * 100)
            .values
        )
        print(f"\n  ▼ {label}")
        print(f"  {'年月':<8} {'賭け数':>6} {'利益':>8} {'累計P&L':>10} {'月ROI%':>8}")
        print("  " + "-" * 48)
        for _, row in monthly.iterrows():
            bar = "▪" * min(int(abs(row["profit"]) / 500), 15)
            sign = "+" if row["profit"] >= 0 else "-"
            print(
                f"  {row['ym']:<8} {row['bets']:>6} {sign}{abs(row['profit']):>7,}円 "
                f"{row['cumulative']:>+10,.0f}円 {row['roi']:>7.1f}%"
            )


# ============================================================
# メイン
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="EV分析・悪条件検出・回収率改善検証")
    parser.add_argument("--start", default="20240101", help="開始日 YYYYMMDD")
    parser.add_argument("--end", default="20261231", help="終了日 YYYYMMDD")
    parser.add_argument("--version", type=int, default=COMPOSITE_VERSION)
    parser.add_argument("--min-runners", type=int, default=4)
    args = parser.parse_args()

    print(f"\n検証期間: {args.start} ~ {args.end}  (v{args.version})")

    df_raw = load_data(args.start, args.end, args.version)
    if df_raw.empty:
        print("データなし")
        return

    df = filter_valid(df_raw, args.min_runners)
    if df.empty:
        print("フィルタ後データなし")
        return

    section_ev(df)
    section_index_power(df)
    bad_results = section_bad_conditions(df)
    section_filter_effect(df, bad_results)

    # フィルター後データを作成してP&L表示
    df2 = df.copy()
    df2["distance_cat"] = df2["distance"].apply(distance_cat)
    df2["head_count_cat"] = df2["head_count"].apply(head_count_cat)
    df2["grade_cat"] = df2.apply(lambda r: grade_cat(r["grade"], r["race_type_code"]), axis=1)

    def extract_bad(tbl, col, t=75.0):
        return tbl.loc[tbl["ROI%"] < t, col].tolist() if not tbl.empty else []

    bad_s_label = extract_bad(bad_results.get("surface", pd.DataFrame()), "surface_label")
    bad_s = [k for k, v in SURFACE_LABEL.items() if v in bad_s_label]
    bad_d = extract_bad(bad_results.get("distance", pd.DataFrame()), "distance_cat")
    bad_g = extract_bad(bad_results.get("grade", pd.DataFrame()), "grade_cat")
    bad_c = extract_bad(bad_results.get("condition", pd.DataFrame()), "condition")
    bad_h = extract_bad(bad_results.get("head_count", pd.DataFrame()), "head_count_cat")

    gap_map = {}
    for race_id, grp in df2.groupby("race_id"):
        sg = grp.sort_values("composite_index", ascending=False).reset_index(drop=True)
        gap_map[race_id] = (
            float(sg.iloc[0]["composite_index"]) - float(sg.iloc[1]["composite_index"])
            if len(sg) >= 2
            else 0.0
        )
    df2["gap_12"] = df2["race_id"].map(gap_map)

    mask = (
        ~df2["surface"].isin(bad_s)
        & ~df2["distance_cat"].isin(bad_d)
        & ~df2["grade_cat"].isin(bad_g)
        & ~df2["condition"].isin(bad_c)
        & ~df2["head_count_cat"].isin(bad_h)
        & (df2["gap_12"] >= 3)
    )
    df_filtered = df2[mask]

    section_monthly_pnl(df, df_filtered if not df_filtered.empty else None)


if __name__ == "__main__":
    main()
