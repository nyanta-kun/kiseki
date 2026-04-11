"""バックテストスクリプト

算出済み総合指数と実際のレース結果を照合し、指数の予測精度・収益性を検証する。

機能:
  - 指数1位馬の単勝/複勝的中率
  - スピアマン順位相関（指数順位 vs 着順）
  - 単勝・複勝・馬連 ROIシミュレーション
  - 月別・馬場別・距離別等の内訳集計
  - Markdownレポート出力

使い方:
  python scripts/backtest.py --start 20260101 --end 20260322
  python scripts/backtest.py --start 20260101 --end 20260322 --breakdown surface
  python scripts/backtest.py --start 20260101 --end 20260322 --report docs/verification/
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.db.session import engine
from src.indices.composite import COMPOSITE_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backtest")


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------


def _build_query(version: int) -> text:
    return text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course_name     AS course_name,
    r.grade           AS grade,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    ci.horse_id       AS horse_id,
    ci.composite_index    AS composite_index,
    ci.speed_index        AS speed_index,
    ci.last_3f_index      AS last3f_index,
    ci.course_aptitude    AS course_aptitude,
    ci.position_advantage AS position_advantage,
    ci.jockey_index       AS jockey_index,
    ci.pace_index         AS pace_index,
    ci.rotation_index     AS rotation_index,
    ci.pedigree_index     AS pedigree_index,
    ci.training_index     AS training_index,
    ci.anagusa_index              AS anagusa_index,
    ci.paddock_index              AS paddock_index,
    ci.rebound_index              AS rebound_index,
    ci.rivals_growth_index        AS rivals_growth_index,
    ci.career_phase_index         AS career_phase_index,
    ci.distance_change_index      AS distance_change_index,
    ci.jockey_trainer_combo_index AS jockey_trainer_combo_index,
    ci.going_pedigree_index       AS going_pedigree_index,
    rr.finish_position            AS finish_position,
    rr.abnormality_code           AS abnormality_code,
    rr.win_odds                   AS win_odds,
    rr.win_popularity             AS win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND ci.version = {version}
ORDER BY r.date, r.id, ci.horse_id
""")


def load_data(start_date: str, end_date: str, version: int = COMPOSITE_VERSION) -> pd.DataFrame:
    """DBから算出指数と実績を結合して取得する。"""
    with Session(engine) as db:
        result = db.execute(_build_query(version), {"start_date": start_date, "end_date": end_date})
        rows = result.fetchall()
        columns = result.keys()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=list(columns))

    # 型変換
    for col in [
        "composite_index",
        "speed_index",
        "last3f_index",
        "course_aptitude",
        "position_advantage",
        "jockey_index",
        "pace_index",
        "rotation_index",
        "pedigree_index",
        "training_index",
        "anagusa_index",
        "paddock_index",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")

    logger.info(f"取得レコード数: {len(df):,} 件 / レース数: {df['race_id'].nunique():,}")
    return df


# ---------------------------------------------------------------------------
# フィルタリング
# ---------------------------------------------------------------------------


def filter_valid_races(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """バックテストに使用するレースを絞り込む。

    除外条件:
    - 異常コードあり馬が含まれるレース（競走除外等）
    - 着順に NULL を含むレース
    - 出走頭数が min_runners 未満のレース
    - 指数が未算出（composite_index=NULL）の馬を含むレース
    """
    # 異常・着順なし の馬がいるレースを除外
    bad_races = df[
        (df["abnormality_code"] > 0) | df["finish_position"].isna() | df["composite_index"].isna()
    ]["race_id"].unique()

    df = df[~df["race_id"].isin(bad_races)].copy()

    # 頭数フィルタ
    race_counts = df.groupby("race_id")["horse_id"].count()
    valid_races = race_counts[race_counts >= min_runners].index
    df = df[df["race_id"].isin(valid_races)].copy()

    logger.info(
        f"フィルタ後: {len(df):,} 件 / "
        f"有効レース: {df['race_id'].nunique():,} / "
        f"除外レース: {len(bad_races):,}"
    )
    return df


# ---------------------------------------------------------------------------
# メトリクス算出
# ---------------------------------------------------------------------------

INDEX_COLS = [
    "composite_index",
    "speed_index",
    "last3f_index",
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


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """2配列のスピアマン順位相関係数を返す。"""
    rx = x.argsort().argsort().astype(float)
    ry = y.argsort().argsort().astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


INDEX_LABELS = {
    "composite_index": "総合",
    "speed_index": "スピード",
    "last3f_index": "後3F",
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


def _spearman_per_race(df: pd.DataFrame, index_col: str) -> list[float]:
    """レースごとのスピアマン相関係数リストを返す（higher index = better rank）。"""
    rhos = []
    for _, grp in df.groupby("race_id"):
        if len(grp) < 3:
            continue
        x = grp[index_col].to_numpy(dtype=float)
        y = grp["finish_position"].to_numpy(dtype=float)
        if np.any(np.isnan(x)) or np.any(np.isnan(y)):
            continue
        rho = _spearman(x, y)
        if not np.isnan(rho):
            rhos.append(rho)
    return rhos


def compute_metrics(df: pd.DataFrame) -> dict:
    """全体メトリクスを算出する。

    Returns:
        {
          "n_races": int,
          "n_horses": int,
          "win_rate": float,       # 指数1位馬の単勝的中率
          "place_rate": float,     # 指数1位馬の複勝的中率（3着以内）
          "random_win": float,     # ランダム選択の単勝的中率（1/平均頭数）
          "random_place": float,   # ランダム選択の複勝的中率（3/平均頭数）
          "index_stats": {         # 各指数のスピアマン相関
              "composite_index": {"mean_rho": float, "median_rho": float, "n": int},
              ...
          }
        }
    """
    n_races = df["race_id"].nunique()
    n_horses = len(df)

    # 指数1位馬の抽出
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()]
    win_rate = (top1["finish_position"] == 1).mean()
    place_rate = (top1["finish_position"] <= 3).mean()

    avg_runners = df.groupby("race_id")["horse_id"].count().mean()
    random_win = 1.0 / avg_runners
    random_place = 3.0 / avg_runners

    # 各指数のスピアマン相関
    index_stats: dict[str, dict] = {}
    for col in INDEX_COLS:
        if df[col].isna().all():
            continue
        rhos = _spearman_per_race(df, col)
        if rhos:
            index_stats[col] = {
                "mean_rho": float(np.mean(rhos)),
                "median_rho": float(np.median(rhos)),
                "n": len(rhos),
            }

    return {
        "n_races": n_races,
        "n_horses": n_horses,
        "win_rate": float(win_rate),
        "place_rate": float(place_rate),
        "random_win": float(random_win),
        "random_place": float(random_place),
        "avg_runners": float(avg_runners),
        "index_stats": index_stats,
    }


def compute_roi(df: pd.DataFrame) -> dict:
    """馬券種別ROIを計算する（100円均一購入想定）。

    対象馬券:
      - 単勝: 指数1位を購入
      - 複勝: 指数1位を購入（払戻はwin_odds × 0.4 で近似）
      - 馬連: 指数Top2を購入（払戻なし・勝率の参考値のみ）

    Returns:
        {"tansho": {...}, "fukusho": {...}, "umaren": {...}}
    """
    result: dict[str, dict] = {}

    # 単勝
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    bets = len(valid)
    wins = (valid["finish_position"] == 1).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    roi = float(payout / bets * 100) if bets > 0 else 0.0
    avg_odds = (
        float(valid.loc[valid["finish_position"] == 1, "win_odds"].mean()) if wins > 0 else 0.0
    )
    result["tansho"] = {
        "bets": bets,
        "wins": int(wins),
        "roi_pct": round(roi, 1),
        "win_rate": round(float(wins / bets) * 100, 1) if bets > 0 else 0.0,
        "avg_odds": round(avg_odds, 2),
    }

    # 月別累積単勝 P&L（100円単位）
    top1_valid = valid.copy()
    top1_valid["ym"] = top1_valid["date"].astype(str).str[:6]
    top1_valid["profit"] = top1_valid.apply(
        lambda r: r["win_odds"] * 100 - 100 if r["finish_position"] == 1 else -100, axis=1
    )
    monthly = (
        top1_valid.groupby("ym")
        .agg(bets=("profit", "count"), profit=("profit", "sum"))
        .reset_index()
    )
    monthly["cumulative"] = monthly["profit"].cumsum()
    result["monthly_tansho"] = monthly.to_dict("records")

    # 馬連（指数Top2が実際の1・2着を占める確率）
    umaren_hits = 0
    umaren_bets = 0
    for _, grp in df.groupby("race_id"):
        grp_sorted = grp.nlargest(2, "composite_index")
        if len(grp_sorted) < 2:
            continue
        top2_ids = set(grp_sorted["horse_id"].tolist())
        finishers = grp[grp["finish_position"].isin([1, 2])]["horse_id"].tolist()
        if len(finishers) == 2:
            umaren_bets += 1
            if set(finishers) == top2_ids:
                umaren_hits += 1
    result["umaren"] = {
        "bets": umaren_bets,
        "hits": umaren_hits,
        "hit_rate": round(umaren_hits / umaren_bets * 100, 1) if umaren_bets > 0 else 0.0,
    }

    return result


def compute_gap_analysis(df: pd.DataFrame) -> dict:
    """指数差（gap）別の予測信頼度を分析する。

    1位と2位の指数差（gap_12）が大きいほど1位馬の的中率・ROIが向上するかを検証する。
    2位と3位の指数差（gap_23）が大きいほど指数Top2が1・2着を占める馬連的中率が向上するかを検証する。

    Returns:
        {
          "gap12": DataFrame,  # gap_12（1-2位差）別 勝率・複勝率・ROI
          "gap23": DataFrame,  # gap_23（2-3位差）別 馬連的中率
          "n_races": int,      # 分析対象レース数
        }
    """
    BUCKET_LABELS = [
        "0〜3未満（拮抗）",
        "3〜6未満（やや優位）",
        "6〜10未満（優位）",
        "10〜15未満（大差）",
        "15以上（支配的）",
    ]

    def _bucket(val: float) -> str:
        if val < 3:
            return BUCKET_LABELS[0]
        if val < 6:
            return BUCKET_LABELS[1]
        if val < 10:
            return BUCKET_LABELS[2]
        if val < 15:
            return BUCKET_LABELS[3]
        return BUCKET_LABELS[4]

    records = []
    for race_id, grp in df.groupby("race_id"):
        if len(grp) < 3:
            continue
        sg = grp.sort_values("composite_index", ascending=False).reset_index(drop=True)
        gap_12 = float(sg.iloc[0]["composite_index"]) - float(sg.iloc[1]["composite_index"])
        gap_23 = float(sg.iloc[1]["composite_index"]) - float(sg.iloc[2]["composite_index"])

        rank1 = sg.iloc[0]
        top2_ids = set(sg.iloc[:2]["horse_id"].tolist())
        finishers_12 = set(grp[grp["finish_position"].isin([1, 2])]["horse_id"].tolist())
        umaren_hit = int(top2_ids == finishers_12 and len(finishers_12) == 2)

        records.append(
            {
                "gap_12": gap_12,
                "gap_23": gap_23,
                "rank1_win": int(rank1["finish_position"] == 1),
                "rank1_place": int(rank1["finish_position"] <= 3),
                "rank1_odds": rank1["win_odds"] if pd.notna(rank1["win_odds"]) else None,
                "umaren_hit": umaren_hit,
                "head_count": len(grp),
            }
        )

    if not records:
        return {"gap12": pd.DataFrame(), "gap23": pd.DataFrame(), "n_races": 0}

    rdf = pd.DataFrame(records)
    rdf["gap12_bucket"] = rdf["gap_12"].apply(_bucket)
    rdf["gap23_bucket"] = rdf["gap_23"].apply(_bucket)

    # ── gap_12 集計 ──────────────────────────────────────────────────
    gap12_rows = []
    for label in BUCKET_LABELS:
        sub = rdf[rdf["gap12_bucket"] == label]
        if len(sub) == 0:
            continue
        n = len(sub)
        win_rate = sub["rank1_win"].mean()
        place_rate = sub["rank1_place"].mean()
        umaren_rate = sub["umaren_hit"].mean()
        avg_hc = sub["head_count"].mean()

        valid = sub[sub["rank1_odds"].notna() & (sub["rank1_odds"] > 0)]
        if len(valid) > 0:
            payout = valid.loc[valid["rank1_win"] == 1, "rank1_odds"].sum()
            roi = float(payout / len(valid) * 100)
            avg_odds_hit = valid.loc[valid["rank1_win"] == 1, "rank1_odds"].mean()
        else:
            roi = float("nan")
            avg_odds_hit = float("nan")

        gap12_rows.append(
            {
                "指数差(1-2位)": label,
                "レース数": n,
                "単勝的中率": win_rate,
                "複勝的中率": place_rate,
                "馬連的中率": umaren_rate,
                "ランダム単勝": 1.0 / avg_hc if avg_hc > 0 else float("nan"),
                "ROI(%)": round(roi, 1),
                "平均配当(的中時)": round(avg_odds_hit, 2)
                if not np.isnan(avg_odds_hit)
                else float("nan"),
            }
        )

    # ── gap_23 集計 ──────────────────────────────────────────────────
    gap23_rows = []
    for label in BUCKET_LABELS:
        sub = rdf[rdf["gap23_bucket"] == label]
        if len(sub) == 0:
            continue
        n = len(sub)
        gap23_rows.append(
            {
                "指数差(2-3位)": label,
                "レース数": n,
                "単勝的中率": sub["rank1_win"].mean(),
                "複勝的中率": sub["rank1_place"].mean(),
                "馬連的中率": sub["umaren_hit"].mean(),
            }
        )

    return {
        "gap12": pd.DataFrame(gap12_rows),
        "gap23": pd.DataFrame(gap23_rows),
        "n_races": len(rdf),
    }


def compute_breakdown(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """指定軸での指数1位馬単勝/複勝的中率を集計する。

    Args:
        by: "surface" | "distance_cat" | "grade" | "course_name"

    Returns:
        集計 DataFrame
    """
    if by == "distance_cat":

        def _cat(d: int) -> str:
            if d <= 1400:
                return "スプリント(～1400)"
            if d <= 1800:
                return "マイル(1401-1800)"
            if d <= 2400:
                return "中距離(1801-2400)"
            return "長距離(2401～)"

        df = df.copy()
        df["_key"] = df["distance"].apply(_cat)
    elif by == "surface":
        df = df.copy()
        df["_key"] = df["surface"].apply(
            lambda s: (
                "芝"
                if isinstance(s, str) and s.startswith("芝")
                else "ダート"
                if isinstance(s, str) and s.startswith("ダ")
                else "その他"
            )
        )
    else:
        df = df.copy()
        df["_key"] = df[by].fillna("不明")

    rows = []
    for key, grp in df.groupby("_key"):
        top1 = grp.loc[grp.groupby("race_id")["composite_index"].idxmax()]
        n = len(top1)
        win = (top1["finish_position"] == 1).sum()
        place = (top1["finish_position"] <= 3).sum()
        avg_r = grp.groupby("race_id")["horse_id"].count().mean()
        rhos = _spearman_per_race(grp, "composite_index")
        rows.append(
            {
                "カテゴリ": key,
                "レース数": n,
                "単勝的中率": win / n if n > 0 else 0,
                "複勝的中率": place / n if n > 0 else 0,
                "ランダム単勝": 1 / avg_r if avg_r > 0 else 0,
                "スピアマン相関(中央値)": float(np.median(rhos)) if rhos else float("nan"),
            }
        )
    return pd.DataFrame(rows).sort_values("レース数", ascending=False)


# ---------------------------------------------------------------------------
# 表示・出力
# ---------------------------------------------------------------------------


def print_summary(m: dict, start_date: str, end_date: str) -> None:
    """メトリクスサマリーをコンソールへ出力する。"""
    print("\n" + "=" * 65)
    print(f"  バックテスト結果  {start_date} ～ {end_date}")
    print("=" * 65)
    print(f"  対象レース数  : {m['n_races']:,}")
    print(f"  対象頭数      : {m['n_horses']:,}")
    print(f"  平均出走頭数  : {m['avg_runners']:.1f} 頭")
    print()
    print("  【指数1位馬 的中率】")
    print(
        f"  単勝的中率   : {m['win_rate']:.1%}  (ランダム: {m['random_win']:.1%}  {m['win_rate'] / m['random_win']:.2f}倍)"
    )
    print(
        f"  複勝的中率   : {m['place_rate']:.1%}  (ランダム: {m['random_place']:.1%}  {m['place_rate'] / m['random_place']:.2f}倍)"
    )
    print()
    print("  【各指数 スピアマン順位相関】")
    print(f"  {'指数':<14}  {'平均ρ':>8}  {'中央値ρ':>8}  {'サンプル':>8}")
    print("  " + "-" * 44)

    stats = m["index_stats"]
    # composite を先頭に、残りは mean_rho 降順
    ordered = ["composite_index"] + sorted(
        [c for c in INDEX_COLS if c != "composite_index" and c in stats],
        key=lambda c: stats[c]["mean_rho"],
        reverse=True,
    )
    for col in ordered:
        if col not in stats:
            continue
        s = stats[col]
        label = INDEX_LABELS.get(col, col)
        print(f"  {label:<14}  {s['mean_rho']:>8.4f}  {s['median_rho']:>8.4f}  {s['n']:>8,}")
    print("=" * 65)


def print_breakdown(bdf: pd.DataFrame, title: str) -> None:
    """内訳テーブルをコンソールへ出力する。"""
    print(f"\n  【{title}別内訳】")
    print(
        f"  {'カテゴリ':<22}  {'レース':>6}  {'単勝%':>7}  {'複勝%':>7}  {'ランダム%':>9}  {'相関ρ':>7}"
    )
    print("  " + "-" * 65)
    for _, row in bdf.iterrows():
        print(
            f"  {str(row['カテゴリ']):<22}  "
            f"{int(row['レース数']):>6}  "
            f"{row['単勝的中率']:>7.1%}  "
            f"{row['複勝的中率']:>7.1%}  "
            f"{row['ランダム単勝']:>9.1%}  "
            f"{row['スピアマン相関(中央値)']:>7.4f}"
        )


def print_gap_analysis(gap: dict) -> None:
    """指数差分析結果をコンソールへ出力する。"""
    if gap["gap12"].empty:
        return

    print(f"\n  【指数差（1-2位）別 信頼度分析】  ※ n={gap['n_races']:,}レース")
    print(
        f"  {'指数差':22}  {'レース':>6}  {'単勝%':>7}  {'複勝%':>7}  {'ランダム%':>9}  {'馬連%':>7}  {'ROI%':>7}  {'平均配当':>8}"
    )
    print("  " + "-" * 83)
    for _, row in gap["gap12"].iterrows():
        roi_str = f"{row['ROI(%)']:>7.1f}" if not np.isnan(row["ROI(%)"]) else "    N/A"
        odds_str = (
            f"{row['平均配当(的中時)']:>8.1f}"
            if not np.isnan(row["平均配当(的中時)"])
            else "     N/A"
        )
        print(
            f"  {str(row['指数差(1-2位)']):22}  "
            f"{int(row['レース数']):>6}  "
            f"{row['単勝的中率']:>7.1%}  "
            f"{row['複勝的中率']:>7.1%}  "
            f"{row['ランダム単勝']:>9.1%}  "
            f"{row['馬連的中率']:>7.1%}  "
            f"{roi_str}  {odds_str}"
        )

    if not gap["gap23"].empty:
        print("\n  【指数差（2-3位）別 馬連信頼度分析】")
        print(f"  {'指数差':22}  {'レース':>6}  {'単勝%':>7}  {'複勝%':>7}  {'馬連%':>7}")
        print("  " + "-" * 60)
        for _, row in gap["gap23"].iterrows():
            print(
                f"  {str(row['指数差(2-3位)']):22}  "
                f"{int(row['レース数']):>6}  "
                f"{row['単勝的中率']:>7.1%}  "
                f"{row['複勝的中率']:>7.1%}  "
                f"{row['馬連的中率']:>7.1%}"
            )


def build_markdown_report(
    m: dict,
    roi: dict,
    gap: dict,
    bdf_surface: pd.DataFrame,
    bdf_dist: pd.DataFrame,
    start_date: str,
    end_date: str,
    weights_label: str = "現行",
) -> str:
    """バックテスト結果をMarkdown形式で組み立てる。"""
    from datetime import date as _date

    today = _date.today().strftime("%Y-%m-%d")
    random_win = m["random_win"]
    random_place = m["random_place"]
    ts = roi["tansho"]
    um = roi["umaren"]

    lines = [
        f"# バックテストレポート — {start_date[:4]}年{start_date[4:6]}月{start_date[6:]}日 〜 {end_date[:4]}年{end_date[4:6]}月{end_date[6:]}日",
        "",
        f"**検証日**: {today}  ",
        f"**重みセット**: {weights_label}  ",
        "**対象**: 4頭以上・異常コードなし・指数算出済みレース",
        "",
        "---",
        "",
        "## 1. 基本統計",
        "",
        "| 項目 | 値 |",
        "|------|----|",
        f"| 対象レース数 | {m['n_races']:,} |",
        f"| 対象頭数（延べ） | {m['n_horses']:,} |",
        f"| 平均出走頭数 | {m['avg_runners']:.1f} 頭 |",
        f"| ランダム期待単勝率 | {random_win:.1%} |",
        f"| ランダム期待複勝率 | {random_place:.1%} |",
        "",
        "---",
        "",
        "## 2. 指数1位馬 的中率",
        "",
        "| 指標 | 値 | ランダム比 |",
        "|------|-----|---------|",
        f"| 単勝的中率 | **{m['win_rate']:.1%}** | ×{m['win_rate'] / random_win:.2f} |",
        f"| 複勝的中率 | **{m['place_rate']:.1%}** | ×{m['place_rate'] / random_place:.2f} |",
        "",
        "---",
        "",
        "## 3. 単勝ROIシミュレーション（指数1位・100円均一）",
        "",
        "> 控除率20%（単勝）の理論ROI = **80%**",
        "",
        "| 項目 | 値 |",
        "|------|----|",
        f"| 購入レース数 | {ts['bets']:,} |",
        f"| 的中数 | {ts['wins']} |",
        f"| 的中率 | {ts['win_rate']}% |",
        f"| **ROI** | **{ts['roi_pct']}%** |",
        f"| 平均配当オッズ | {ts['avg_odds']}倍 |",
        "",
        "### 月別 単勝 P&L（累積, 100円単位）",
        "",
        "| 月 | 件数 | 損益 | 累積損益 |",
        "|-----|------|------|---------|",
    ]
    for r in roi["monthly_tansho"]:
        ym = str(r["ym"])
        month_label = f"{ym[:4]}年{ym[4:6]}月"
        sign = "+" if r["profit"] >= 0 else ""
        sign_c = "+" if r["cumulative"] >= 0 else ""
        lines.append(
            f"| {month_label} | {r['bets']} | {sign}{r['profit']:,}円 | {sign_c}{r['cumulative']:,}円 |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. 馬連シミュレーション（指数Top2）",
        "",
        "| 項目 | 値 |",
        "|------|----|",
        f"| 購入レース数 | {um['bets']:,} |",
        f"| 的中数 | {um['hits']} |",
        f"| 的中率 | **{um['hit_rate']}%** |",
        "",
        "---",
        "",
        "## 5. 各指数 スピアマン順位相関",
        "",
        "| 指数 | 平均ρ | 中央値ρ | サンプル |",
        "|------|-------|--------|---------|",
    ]

    stats = m["index_stats"]
    ordered = ["composite_index"] + sorted(
        [c for c in INDEX_COLS if c != "composite_index" and c in stats],
        key=lambda c: stats[c]["mean_rho"],
        reverse=True,
    )
    for col in ordered:
        if col not in stats:
            continue
        s = stats[col]
        label = INDEX_LABELS.get(col, col)
        lines.append(f"| {label} | {s['mean_rho']:.4f} | {s['median_rho']:.4f} | {s['n']:,} |")

    lines += [
        "",
        "---",
        "",
        "## 6. 馬場別内訳（指数1位）",
        "",
        "| 馬場 | レース数 | 単勝率 | 複勝率 | ランダム単勝 | 相関ρ(中央値) |",
        "|------|---------|--------|--------|------------|-------------|",
    ]
    for _, row in bdf_surface.iterrows():
        lines.append(
            f"| {row['カテゴリ']} | {int(row['レース数'])} "
            f"| {row['単勝的中率']:.1%} | {row['複勝的中率']:.1%} "
            f"| {row['ランダム単勝']:.1%} | {row['スピアマン相関(中央値)']:.4f} |"
        )

    lines += [
        "",
        "## 7. 距離カテゴリ別内訳（指数1位）",
        "",
        "| 距離 | レース数 | 単勝率 | 複勝率 | ランダム単勝 | 相関ρ(中央値) |",
        "|------|---------|--------|--------|------------|-------------|",
    ]
    for _, row in bdf_dist.iterrows():
        lines.append(
            f"| {row['カテゴリ']} | {int(row['レース数'])} "
            f"| {row['単勝的中率']:.1%} | {row['複勝的中率']:.1%} "
            f"| {row['ランダム単勝']:.1%} | {row['スピアマン相関(中央値)']:.4f} |"
        )

    # ── gap分析セクション ──────────────────────────────────────────
    if not gap["gap12"].empty:
        lines += [
            "",
            "---",
            "",
            "## 8. 指数差（1-2位）別 信頼度分析",
            "",
            "> 1位と2位の指数差が大きいほど、1位馬の信頼度が上がるかを検証。",
            "",
            "| 指数差 | レース数 | 単勝率 | 複勝率 | ランダム単勝 | 馬連率 | ROI(%) | 平均配当 |",
            "|--------|---------|--------|--------|------------|--------|--------|---------|",
        ]
        for _, row in gap["gap12"].iterrows():
            roi_str = f"{row['ROI(%)']:.1f}" if not np.isnan(row["ROI(%)"]) else "N/A"
            odds_str = (
                f"{row['平均配当(的中時)']:.1f}倍"
                if not np.isnan(row["平均配当(的中時)"])
                else "N/A"
            )
            lines.append(
                f"| {row['指数差(1-2位)']} | {int(row['レース数'])} "
                f"| {row['単勝的中率']:.1%} | {row['複勝的中率']:.1%} "
                f"| {row['ランダム単勝']:.1%} | {row['馬連的中率']:.1%} "
                f"| {roi_str} | {odds_str} |"
            )

    if not gap["gap23"].empty:
        lines += [
            "",
            "## 9. 指数差（2-3位）別 馬連信頼度分析",
            "",
            "> 2位と3位の差が大きい＝上位2頭の優位性が明確なときの馬連的中率を検証。",
            "",
            "| 指数差 | レース数 | 単勝率 | 複勝率 | 馬連率 |",
            "|--------|---------|--------|--------|--------|",
        ]
        for _, row in gap["gap23"].iterrows():
            lines.append(
                f"| {row['指数差(2-3位)']} | {int(row['レース数'])} "
                f"| {row['単勝的中率']:.1%} | {row['複勝的中率']:.1%} "
                f"| {row['馬連的中率']:.1%} |"
            )

    lines.append("")
    lines.append("---")
    lines.append("*Generated by kiseki/backend/scripts/backtest.py*")

    return "\n".join(lines)


def save_csv(df: pd.DataFrame, output_path: str) -> None:
    """レース単位の詳細結果をCSVへ出力する。"""
    # 指数1位馬 + 実際の1着馬 を抽出してサマリー行を作成
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    top1["top1_win"] = (top1["finish_position"] == 1).astype(int)
    top1["top1_place"] = (top1["finish_position"] <= 3).astype(int)
    top1 = top1[
        [
            "race_id",
            "date",
            "course_name",
            "grade",
            "surface",
            "distance",
            "head_count",
            "composite_index",
            "speed_index",
            "course_aptitude",
            "jockey_index",
            "pace_index",
            "rotation_index",
            "pedigree_index",
            "finish_position",
            "top1_win",
            "top1_place",
        ]
    ]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    top1.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"CSV出力完了: {path} ({len(top1)} 行)")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def run(
    start_date: str,
    end_date: str,
    output_path: str | None,
    breakdown: str | None,
    report_dir: str | None,
    weights_label: str = "現行",
    version: int = COMPOSITE_VERSION,
) -> None:
    """バックテストを実行する。"""
    logger.info(f"バックテスト開始: {start_date} ～ {end_date} (version={version})")

    df = load_data(start_date, end_date, version=version)
    if df.empty:
        logger.warning("データなし。日付範囲・算出済みデータを確認してください。")
        return

    df = filter_valid_races(df)
    if df.empty:
        logger.warning("有効なレースが見つかりませんでした。")
        return

    metrics = compute_metrics(df)
    print_summary(metrics, start_date, end_date)

    roi = compute_roi(df)
    ts = roi["tansho"]
    um = roi["umaren"]
    print("\n  【単勝ROI（指数1位・100円均一）】")
    print(
        f"  購入: {ts['bets']:,}レース  的中: {ts['wins']}  ROI: {ts['roi_pct']}%  "
        f"平均配当: {ts['avg_odds']}倍"
    )
    print("\n  【馬連的中率（指数Top2）】")
    print(f"  購入: {um['bets']:,}レース  的中: {um['hits']}  的中率: {um['hit_rate']}%")

    gap = compute_gap_analysis(df)
    print_gap_analysis(gap)

    if breakdown:
        bdf = compute_breakdown(df, breakdown)
        titles = {
            "surface": "馬場",
            "distance_cat": "距離カテゴリ",
            "grade": "グレード",
            "course_name": "競馬場",
        }
        print_breakdown(bdf, titles.get(breakdown, breakdown))

    if report_dir:
        from datetime import date as _date

        bdf_surface = compute_breakdown(df, "surface")
        bdf_dist = compute_breakdown(df, "distance_cat")
        report_md = build_markdown_report(
            metrics,
            roi,
            gap,
            bdf_surface,
            bdf_dist,
            start_date,
            end_date,
            weights_label,
        )
        report_path = (
            Path(report_dir)
            / f"{_date.today().strftime('%Y%m%d')}_{start_date}_{end_date}_backtest.md"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")
        logger.info(f"Markdownレポート出力: {report_path}")

    if output_path:
        save_csv(df, output_path)


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="バックテスト: 指数と実績の照合")
    parser.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    parser.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    parser.add_argument("--output", default=None, help="CSV出力先パス")
    parser.add_argument(
        "--breakdown",
        choices=["surface", "distance_cat", "grade", "course_name"],
        default=None,
        help="内訳集計軸 (surface / distance_cat / grade / course_name)",
    )
    parser.add_argument("--report", default=None, help="Markdownレポート出力先ディレクトリ")
    parser.add_argument("--weights-label", default="現行", help="レポートに記載する重みセット名")
    parser.add_argument(
        "--version",
        type=int,
        default=COMPOSITE_VERSION,
        help=f"算出バージョン (default: {COMPOSITE_VERSION})",
    )
    args = parser.parse_args()

    for d in (args.start, args.end):
        if len(d) != 8 or not d.isdigit():
            parser.error("日付は YYYYMMDD 形式で指定してください")

    run(
        args.start,
        args.end,
        args.output,
        args.breakdown,
        args.report,
        args.weights_label,
        args.version,
    )


if __name__ == "__main__":
    main()
