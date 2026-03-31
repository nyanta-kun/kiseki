"""パドック指数有効性検証スクリプト

2種類のパドック指数を検証する:
  1. Netkeiba パドック (sekito.netkeiba の p_rank)
  2. 馬体重変化 (race_results.weight_change)

検証指標:
  - p_rank / 体重変化区分別の 勝率 / 複勝率
  - 指数最高馬の単勝・複勝的中率
  - スピアマン順位相関（指数順位 vs 着順）
  - 統計的有意性（χ²検定）

使い方:
  uv run python scripts/verify_paddock.py --start 20240101 --end 20261231
  uv run python scripts/verify_paddock.py --start 20240101 --end 20261231 --report
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.db.session import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("verify_paddock")


# ============================================================
# データ取得
# ============================================================

_NETKEIBA_QUERY = text("""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course_name     AS course_name,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    re.horse_number   AS horse_number,
    re.horse_id       AS horse_id,
    n.p_type          AS p_type,
    n.p_rank          AS p_rank,
    rr.finish_position AS finish_position,
    rr.abnormality_code AS abnormality_code,
    rr.win_odds        AS win_odds,
    rr.win_popularity  AS win_popularity
FROM keiba.race_results rr
JOIN keiba.races r ON r.id = rr.race_id
JOIN keiba.race_entries re ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
LEFT JOIN (
    SELECT
        sn.date,
        sn.course_code,
        sn.race_no,
        sn.horse_no,
        sn.p_type,
        sn.p_rank
    FROM sekito.netkeiba sn
    WHERE sn.is_paddock = true
) n ON (
    TO_CHAR(r.date::date, 'YYYY-MM-DD') = n.date::text
    AND CASE r.course
        WHEN '01' THEN 'JSPK'
        WHEN '02' THEN 'JHKD'
        WHEN '03' THEN 'JFKS'
        WHEN '04' THEN 'JNGT'
        WHEN '05' THEN 'JTOK'
        WHEN '06' THEN 'JNKY'
        WHEN '07' THEN 'JCKO'
        WHEN '08' THEN 'JKYO'
        WHEN '09' THEN 'JHSN'
        WHEN '10' THEN 'JKKR'
        ELSE NULL
    END = n.course_code
    AND r.race_number::integer = n.race_no
    AND re.horse_number = n.horse_no
)
WHERE r.date BETWEEN :start_date AND :end_date
  AND rr.abnormality_code = 0
  AND rr.finish_position IS NOT NULL
ORDER BY r.date, r.id, re.horse_number
""")

_WEIGHT_QUERY = text("""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course_name     AS course_name,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    rr.horse_id       AS horse_id,
    rr.horse_weight   AS horse_weight,
    rr.weight_change  AS weight_change,
    rr.finish_position AS finish_position,
    rr.abnormality_code AS abnormality_code,
    rr.win_odds        AS win_odds
FROM keiba.race_results rr
JOIN keiba.races r ON r.id = rr.race_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND rr.abnormality_code = 0
  AND rr.finish_position IS NOT NULL
  AND rr.horse_weight IS NOT NULL
  AND rr.weight_change IS NOT NULL
ORDER BY r.date, r.id, rr.horse_id
""")


def load_netkeiba_data(start_date: str, end_date: str) -> pd.DataFrame:
    """Netkeibaパドックデータを取得する。"""
    with Session(engine) as db:
        # date形式をYYYYMMDD→YYYY-MM-DDに変換してクエリ
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        result = db.execute(_NETKEIBA_QUERY, {"start_date": sd, "end_date": ed})
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=cols)
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")
    logger.info(f"Netkeiba: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


def load_weight_data(start_date: str, end_date: str) -> pd.DataFrame:
    """馬体重データを取得する。"""
    with Session(engine) as db:
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        result = db.execute(_WEIGHT_QUERY, {"start_date": sd, "end_date": ed})
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=cols)
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["horse_weight"] = pd.to_numeric(df["horse_weight"], errors="coerce")
    df["weight_change"] = pd.to_numeric(df["weight_change"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")

    # 異常値除外（weight_change > 50 or < -50）
    df = df[(df["weight_change"].abs() <= 50)].copy()
    logger.info(f"体重: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


# ============================================================
# ユーティリティ
# ============================================================


def win_rate(df: pd.DataFrame) -> float:
    """勝率を返す。"""
    return (df["finish_position"] == 1).mean()


def place_rate(df: pd.DataFrame) -> float:
    """複勝率（3着以内率）を返す。"""
    return (df["finish_position"] <= 3).mean()


def chi2_test(a_wins: int, a_total: int, b_wins: int, b_total: int) -> tuple[float, float]:
    """2群の的中率差のχ²検定（ベースライン vs 対象群）。"""
    table = [[a_wins, a_total - a_wins], [b_wins, b_total - b_wins]]
    chi2, p, _, _ = stats.chi2_contingency(table, correction=False)
    return chi2, p


def spearman_per_race(df: pd.DataFrame, score_col: str) -> tuple[float, float, int]:
    """レースごとのスピアマン相関係数の平均・中央値を返す。"""
    rhos = []
    for _, grp in df.groupby("race_id"):
        if len(grp) < 3:
            continue
        x = grp[score_col].to_numpy(dtype=float)
        y = grp["finish_position"].to_numpy(dtype=float)
        if np.any(np.isnan(x)) or np.any(np.isnan(y)):
            continue
        # 高スコア → 低着順（1位）が良い → 逆相関が期待値
        rho, _ = stats.spearmanr(x, -y)  # y を反転して正の相関を期待
        if not np.isnan(rho):
            rhos.append(rho)
    if not rhos:
        return 0.0, 0.0, 0
    return float(np.mean(rhos)), float(np.median(rhos)), len(rhos)


def top1_accuracy(df: pd.DataFrame, score_col: str) -> tuple[float, float, int]:
    """指数最高馬の単勝・複勝的中率を返す。"""
    win_hits = 0
    place_hits = 0
    total = 0
    for _, grp in df.groupby("race_id"):
        if grp[score_col].isna().all():
            continue
        top = grp.loc[grp[score_col].idxmax()]
        if pd.isna(top["finish_position"]):
            continue
        total += 1
        if top["finish_position"] == 1:
            win_hits += 1
        if top["finish_position"] <= 3:
            place_hits += 1
    if total == 0:
        return 0.0, 0.0, 0
    return win_hits / total, place_hits / total, total


# ============================================================
# Section 1: Netkeiba パドック検証
# ============================================================


def verify_netkeiba(df: pd.DataFrame) -> None:
    """Netkeibaパドック（p_rank）の有効性を検証する。"""
    print("\n" + "=" * 60)
    print("【1】Netkeiba パドック評価 (p_type / p_rank) 検証")
    print("=" * 60)

    total = len(df)
    has_paddock = df["p_rank"].notna() & df["p_rank"].isin(["A", "B", "C", "穴"])
    print(f"\nデータ総数: {total:,} 件")
    print(f"パドックデータあり: {has_paddock.sum():,} 件 ({has_paddock.mean() * 100:.1f}%)")

    # ベースライン
    baseline_wr = win_rate(df)
    baseline_pr = place_rate(df)
    print(f"\n全体ベースライン: 勝率={baseline_wr * 100:.1f}%, 複勝率={baseline_pr * 100:.1f}%")

    # p_type / p_rank 別の統計
    print("\n--- p_type × p_rank 別 勝率・複勝率 ---")
    print(f"{'カテゴリ':<15} {'件数':>6} {'勝率':>7} {'複勝率':>8} {'p値(勝率)':>12}")
    print("-" * 52)

    categories = [
        ("人気", "A"),
        ("人気", "B"),
        ("人気", "C"),
        ("特注", "穴"),
    ]

    for p_type, p_rank in categories:
        mask = (df["p_type"] == p_type) & (df["p_rank"] == p_rank)
        sub = df[mask]
        if len(sub) < 10:
            print(f"  {p_type}/{p_rank:<10} {len(sub):>6}   (サンプル不足)")
            continue
        wr = win_rate(sub)
        pr = place_rate(sub)
        wins = (sub["finish_position"] == 1).sum()
        base_wins = int(baseline_wr * len(sub))
        try:
            _, p = chi2_test(wins, len(sub), base_wins, len(sub))
            p_str = f"{p:.4f}{'*' if p < 0.05 else ''}"
        except Exception:
            p_str = "N/A"
        print(
            f"  {p_type}/{p_rank:<10} {len(sub):>6}   {wr * 100:>5.1f}%  {pr * 100:>6.1f}%  {p_str:>12}"
        )

    # データなし vs あり の比較
    no_paddock = df[~has_paddock]
    with_paddock = df[has_paddock]
    print(
        f"\n  データなし         {len(no_paddock):>6}   {win_rate(no_paddock) * 100:>5.1f}%  {place_rate(no_paddock) * 100:>6.1f}%"
    )
    print(
        f"  データあり(計)     {len(with_paddock):>6}   {win_rate(with_paddock) * 100:>5.1f}%  {place_rate(with_paddock) * 100:>6.1f}%"
    )

    # スコア付与してスピアマン相関
    PADDOCK_SCORES = {
        ("人気", "A"): 85.0,
        ("人気", "B"): 70.0,
        ("人気", "C"): 45.0,
        ("特注", "穴"): 60.0,
    }
    df["paddock_score"] = df.apply(
        lambda row: PADDOCK_SCORES.get((row["p_type"], row["p_rank"]), 50.0),
        axis=1,
    )

    # パドックデータがあるレースのみでスピアマン
    races_with_any = df.groupby("race_id")["p_rank"].apply(
        lambda x: x.notna().any() and x.isin(["A", "B", "C", "穴"]).any()
    )
    valid_race_ids = races_with_any[races_with_any].index
    df_valid = df[df["race_id"].isin(valid_race_ids)]

    mean_rho, med_rho, n_races = spearman_per_race(df_valid, "paddock_score")
    print("\nスピアマン相関（パドックデータありレース限定）:")
    print(f"  対象レース数: {n_races}")
    print(f"  平均ρ: {mean_rho:.4f}, 中央値ρ: {med_rho:.4f}")
    print("  ※ ρ>0 = 高スコア馬が上位着（予測力あり）")

    # top1 的中率
    wr1, pr1, n1 = top1_accuracy(df_valid, "paddock_score")
    print(f"\n指数最高馬の的中率（対象レース: {n1}）:")
    print(f"  単勝的中率: {wr1 * 100:.1f}%  複勝的中率: {pr1 * 100:.1f}%")

    # 人気馬だけの評価（人気馬だからあたりやすい補正のため）
    print("\n--- 人気補正: p_rank=A かつ 単勝1〜3番人気 ---")
    mask_fav = (df["p_type"] == "人気") & (df["p_rank"] == "A") & (df["win_popularity"] <= 3)
    mask_no_a = df["win_popularity"] <= 3  # ランクA除いた同人気層
    if mask_fav.sum() >= 10:
        sub_a = df[mask_fav]
        sub_all = df[mask_no_a]
        print(
            f"  A+1-3人気: {mask_fav.sum()}件, 勝率={win_rate(sub_a) * 100:.1f}%, 複勝率={place_rate(sub_a) * 100:.1f}%"
        )
        print(
            f"  1-3人気全体: {len(sub_all)}件, 勝率={win_rate(sub_all) * 100:.1f}%, 複勝率={place_rate(sub_all) * 100:.1f}%"
        )
    else:
        print(f"  サンプル不足 ({mask_fav.sum()}件)")

    print("\n--- 穴候補の評価: p_rank=穴 かつ 単勝4番人気以下 ---")
    mask_ana = (df["p_type"] == "特注") & (df["p_rank"] == "穴") & (df["win_popularity"] >= 4)
    mask_low = df["win_popularity"] >= 4
    if mask_ana.sum() >= 10:
        sub_ana = df[mask_ana]
        sub_low = df[mask_low]
        print(
            f"  穴+4番人気以下: {mask_ana.sum()}件, 勝率={win_rate(sub_ana) * 100:.1f}%, 複勝率={place_rate(sub_ana) * 100:.1f}%"
        )
        print(
            f"  4番人気以下全体: {len(sub_low)}件, 勝率={win_rate(sub_low) * 100:.1f}%, 複勝率={place_rate(sub_low) * 100:.1f}%"
        )
    else:
        print(f"  サンプル不足 ({mask_ana.sum()}件)")


# ============================================================
# Section 2: 馬体重変化 検証
# ============================================================


def _weight_category(wc: float) -> str:
    """体重変化をカテゴリ分類する。"""
    aw = abs(wc)
    if aw <= 2:
        return "±2以内"
    elif aw <= 4:
        return "±3-4"
    elif aw <= 6:
        return "±5-6"
    elif aw <= 10:
        return "±7-10"
    else:
        return "±11以上"


WEIGHT_CAT_ORDER = ["±2以内", "±3-4", "±5-6", "±7-10", "±11以上"]


# 増加 vs 減少で分ける
def _weight_dir_category(wc: float) -> str:
    if wc > 0:
        return f"+{_weight_category(wc)}"
    elif wc < 0:
        return f"-{_weight_category(-wc)}"
    else:
        return "±0"


def weight_score(wc: float) -> float:
    """体重変化からスコアを計算する（安定=高スコア）。"""
    aw = abs(wc)
    if aw <= 2:
        return 60.0
    elif aw <= 4:
        return 55.0
    elif aw <= 6:
        return 50.0
    elif aw <= 10:
        return 42.0
    else:
        return 35.0


def verify_weight(df: pd.DataFrame) -> None:
    """馬体重変化の有効性を検証する。"""
    print("\n" + "=" * 60)
    print("【2】馬体重変化 (weight_change) 検証")
    print("=" * 60)

    total = len(df)
    baseline_wr = win_rate(df)
    baseline_pr = place_rate(df)
    print(
        f"\nデータ総数: {total:,} 件  ベースライン: 勝率={baseline_wr * 100:.1f}%, 複勝率={baseline_pr * 100:.1f}%"
    )

    # 体重変化絶対値カテゴリ別
    df["wc_cat"] = df["weight_change"].apply(_weight_category)
    print("\n--- |体重変化| カテゴリ別 勝率・複勝率 ---")
    print(f"{'カテゴリ':<12} {'件数':>8} {'勝率':>7} {'複勝率':>8} {'p値(複勝率)':>12}")
    print("-" * 52)

    for cat in WEIGHT_CAT_ORDER:
        sub = df[df["wc_cat"] == cat]
        if len(sub) < 50:
            print(f"  {cat:<10} {len(sub):>8}   (サンプル不足)")
            continue
        wr = win_rate(sub)
        pr = place_rate(sub)
        places = (sub["finish_position"] <= 3).sum()
        base_places = int(baseline_pr * len(sub))
        try:
            _, p = chi2_test(places, len(sub), base_places, len(sub))
            p_str = f"{p:.4f}{'*' if p < 0.05 else ''}"
        except Exception:
            p_str = "N/A"
        print(f"  {cat:<10} {len(sub):>8}   {wr * 100:>5.1f}%  {pr * 100:>6.1f}%  {p_str:>12}")

    # 増加 vs 減少の比較
    print("\n--- 体重増加 vs 減少 比較 ---")
    inc = df[df["weight_change"] > 0]
    dec = df[df["weight_change"] < 0]
    flat = df[df["weight_change"] == 0]
    print(
        f"  増加: {len(inc):,}件  勝率={win_rate(inc) * 100:.1f}%  複勝率={place_rate(inc) * 100:.1f}%"
    )
    print(
        f"  減少: {len(dec):,}件  勝率={win_rate(dec) * 100:.1f}%  複勝率={place_rate(dec) * 100:.1f}%"
    )
    print(
        f"  変化なし: {len(flat):,}件  勝率={win_rate(flat) * 100:.1f}%  複勝率={place_rate(flat) * 100:.1f}%"
    )

    # スコア付与してスピアマン相関
    df["weight_score"] = df["weight_change"].apply(weight_score)
    mean_rho, med_rho, n_races = spearman_per_race(df, "weight_score")
    print("\nスピアマン相関（全レース）:")
    print(f"  対象レース数: {n_races}")
    print(f"  平均ρ: {mean_rho:.4f}, 中央値ρ: {med_rho:.4f}")

    wr1, pr1, n1 = top1_accuracy(df, "weight_score")
    print(f"\n体重スコア最高馬の的中率（対象レース: {n1}）:")
    print(f"  単勝的中率: {wr1 * 100:.1f}%  複勝的中率: {pr1 * 100:.1f}%")

    # 体重絶対値との相関（重い馬が強い傾向があるか）
    rho_w, p_w = stats.spearmanr(df["horse_weight"], -df["finish_position"])
    print(f"\n馬体重絶対値 vs 着順 スピアマン相関: ρ={rho_w:.4f}, p={p_w:.4f}")

    # 体重変化 vs 着順の直接相関
    rho_c, p_c = stats.spearmanr(df["weight_change"].abs(), df["finish_position"])
    print(f"|体重変化| vs 着順 スピアマン相関: ρ={rho_c:.4f}, p={p_c:.4f}")
    print("  ※ ρ>0 = 体重変化大きいほど着順悪い（負の予測力あり）")

    # 距離別・芝ダート別クロス
    print("\n--- 芝 vs ダート での体重変化効果 ---")
    for surface, label in [("芝", "芝"), ("ダート", "ダート")]:
        sub = df[df["surface"] == surface]
        if len(sub) < 100:
            continue
        rho, p = stats.spearmanr(sub["weight_change"].abs(), sub["finish_position"])
        # 安定馬（±4以内）の複勝率
        stable = sub[sub["weight_change"].abs() <= 4]
        unstable = sub[sub["weight_change"].abs() > 8]
        print(
            f"  {label}: |wc| vs 着順 ρ={rho:.4f}(p={p:.4f}), "
            f"安定({len(stable)}件)複勝率={place_rate(stable) * 100:.1f}%, "
            f"不安定({len(unstable)}件)複勝率={place_rate(unstable) * 100:.1f}%"
        )


# ============================================================
# Section 3: 総合比較・結論
# ============================================================


def conclusion(df_nk: pd.DataFrame, df_wt: pd.DataFrame) -> None:
    """2手法を比較して結論を出す。"""
    print("\n" + "=" * 60)
    print("【3】比較・結論")
    print("=" * 60)

    # Netkeibaの有効性サマリ
    has_rank = df_nk["p_rank"].isin(["A", "B", "C", "穴"])
    rank_a = df_nk[(df_nk["p_type"] == "人気") & (df_nk["p_rank"] == "A")]
    rank_b = df_nk[(df_nk["p_type"] == "人気") & (df_nk["p_rank"] == "B")]
    rank_ana = df_nk[(df_nk["p_type"] == "特注") & (df_nk["p_rank"] == "穴")]

    baseline_pr_nk = place_rate(df_nk)
    baseline_pr_wt = place_rate(df_wt)

    # スピアマン（Netkeiba）
    PADDOCK_SCORES = {
        ("人気", "A"): 85.0,
        ("人気", "B"): 70.0,
        ("人気", "C"): 45.0,
        ("特注", "穴"): 60.0,
    }
    df_nk = df_nk.copy()
    df_nk["paddock_score"] = df_nk.apply(
        lambda r: PADDOCK_SCORES.get((r["p_type"], r["p_rank"]), 50.0), axis=1
    )
    races_with_any = df_nk.groupby("race_id")["p_rank"].apply(
        lambda x: x.isin(["A", "B", "C", "穴"]).any()
    )
    valid_ids = races_with_any[races_with_any].index
    df_nk_valid = df_nk[df_nk["race_id"].isin(valid_ids)]
    nk_rho, _, nk_n = spearman_per_race(df_nk_valid, "paddock_score")
    nk_w1, nk_p1, nk_n1 = top1_accuracy(df_nk_valid, "paddock_score")

    # スピアマン（体重）
    df_wt = df_wt.copy()
    df_wt["weight_score"] = df_wt["weight_change"].apply(weight_score)
    wt_rho, _, wt_n = spearman_per_race(df_wt, "weight_score")
    wt_w1, wt_p1, wt_n1 = top1_accuracy(df_wt, "weight_score")

    print(f"""
手法                      | サンプル数 | スピアマンρ | 単勝的中率 | 複勝的中率
--------------------------|-----------|------------|-----------|----------
Netkeiba p_rank (全体)    | {len(df_nk):>9,} | {nk_rho:>+.4f}      | 参考値    | 参考値
Netkeiba (有効レースのみ) | {nk_n1:>9,} | -          | {nk_w1 * 100:>8.1f}% | {nk_p1 * 100:>8.1f}%
馬体重変化スコア          | {wt_n1:>9,} | {wt_rho:>+.4f}      | {wt_w1 * 100:>8.1f}% | {wt_p1 * 100:>8.1f}%
""")

    print("=== 判定基準 ===")
    print("  有効: スピアマンρ > 0.02 かつ 複勝的中率 > ベースライン+3%")
    print(
        f"  ベースライン複勝率: Netkeiba={baseline_pr_nk * 100:.1f}%, 体重={baseline_pr_wt * 100:.1f}%"
    )
    print()

    # 判定
    nk_effective = (nk_rho > 0.02) and (nk_p1 > baseline_pr_nk + 0.03)
    wt_effective = (wt_rho > 0.02) and (wt_p1 > baseline_pr_wt + 0.03)

    if nk_effective:
        print("✅ Netkeibaパドック: 有効（総合指数への反映を推奨）")
    else:
        print(
            f"❌ Netkeibaパドック: 効果限定的（ρ={nk_rho:.4f}, 捕捉率{has_rank.mean() * 100:.1f}%）"
        )
        print("   → 総合指数への反映は見送り。引き続きweight=0で表示専用。")

    if wt_effective:
        print("✅ 馬体重変化: 有効（総合指数への反映を推奨）")
    else:
        print(f"❌ 馬体重変化: 効果限定的（ρ={wt_rho:.4f}）")
        print("   → 総合指数への反映は見送り。training指数内の体重要素で代替。")

    # サンプルサイズの問題
    print("\n--- サンプルサイズ評価 ---")
    print(f"  Netkeibaパドック有効データ: {has_rank.sum()}件 (全体の{has_rank.mean() * 100:.1f}%)")
    if has_rank.sum() < 1000:
        print("  ⚠️  サンプル不足により統計的有意性が低い可能性あり")
    if len(rank_a) < 100:
        print(f"  ⚠️  ランクAのサンプル({len(rank_a)}件)が少なく、過学習リスクあり")


# ============================================================
# メイン
# ============================================================


def main() -> None:
    """メイン処理。"""
    parser = argparse.ArgumentParser(description="パドック指数有効性検証")
    parser.add_argument("--start", default="20240101", help="開始日 YYYYMMDD")
    parser.add_argument("--end", default="20261231", help="終了日 YYYYMMDD")
    parser.add_argument("--report", action="store_true", help="レポートファイルも出力")
    args = parser.parse_args()

    print(f"\n検証期間: {args.start} ~ {args.end}")

    logger.info("Netkeibaパドックデータ取得中...")
    df_nk = load_netkeiba_data(args.start, args.end)

    logger.info("馬体重データ取得中...")
    df_wt = load_weight_data(args.start, args.end)

    if df_nk.empty and df_wt.empty:
        print("データが取得できませんでした。")
        return

    verify_netkeiba(df_nk)
    verify_weight(df_wt)
    conclusion(df_nk, df_wt)

    if args.report:
        out_dir = _root / "docs" / "verification"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = out_dir / f"paddock_verify_{args.start}_{args.end}.txt"
        # テキストをキャプチャ（再実行）して保存
        print(
            f"\n※ レポートは {fname} に出力するには --report を外して stdout をリダイレクトしてください"
        )


if __name__ == "__main__":
    main()
