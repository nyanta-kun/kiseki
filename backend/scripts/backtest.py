"""バックテストスクリプト

算出済み総合指数と実際のレース結果を照合し、指数の予測精度を検証する。

指数1位馬の単勝/複勝的中率、スピアマン順位相関（指数順位 vs 着順）、
各単体指数の予測力比較などを集計して出力する。

使い方:
  python scripts/backtest.py --start 20240101 --end 20241231
  python scripts/backtest.py --start 20240101 --end 20241231 --output /tmp/backtest.csv
  python scripts/backtest.py --start 20240101 --end 20241231 --breakdown surface
"""

from __future__ import annotations

import argparse
import csv
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backtest")


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

_QUERY = text("""
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
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND ci.version = 1
ORDER BY r.date, r.id, ci.horse_id
""")


def load_data(start_date: str, end_date: str) -> pd.DataFrame:
    """DBから算出指数と実績を結合して取得する。"""
    with Session(engine) as db:
        result = db.execute(_QUERY, {"start_date": start_date, "end_date": end_date})
        rows = result.fetchall()
        columns = result.keys()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=list(columns))

    # 型変換
    for col in ["composite_index", "speed_index", "last3f_index", "course_aptitude",
                "position_advantage", "jockey_index", "pace_index", "rotation_index",
                "pedigree_index"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")

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
        (df["abnormality_code"] > 0) |
        df["finish_position"].isna() |
        df["composite_index"].isna()
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
    "composite_index", "speed_index", "last3f_index", "course_aptitude",
    "position_advantage", "jockey_index", "pace_index", "rotation_index",
    "pedigree_index",
]

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """2配列のスピアマン順位相関係数を返す。"""
    rx = x.argsort().argsort().astype(float)
    ry = y.argsort().argsort().astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


INDEX_LABELS = {
    "composite_index":    "総合",
    "speed_index":        "スピード",
    "last3f_index":       "後3F",
    "course_aptitude":    "コース適性",
    "position_advantage": "枠順",
    "jockey_index":       "騎手",
    "pace_index":         "展開",
    "rotation_index":     "ローテ",
    "pedigree_index":     "血統",
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


def compute_breakdown(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """指定軸での指数1位馬単勝/複勝的中率を集計する。

    Args:
        by: "surface" | "distance_cat" | "grade" | "course_name"

    Returns:
        集計 DataFrame
    """
    if by == "distance_cat":
        def _cat(d: int) -> str:
            if d <= 1400: return "スプリント(～1400)"
            if d <= 1800: return "マイル(1401-1800)"
            if d <= 2400: return "中距離(1801-2400)"
            return "長距離(2401～)"
        df = df.copy()
        df["_key"] = df["distance"].apply(_cat)
    elif by == "surface":
        df = df.copy()
        df["_key"] = df["surface"].apply(
            lambda s: "芝" if isinstance(s, str) and s.startswith("芝") else
                      "ダート" if isinstance(s, str) and s.startswith("ダ") else "その他"
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
        rows.append({
            "カテゴリ": key,
            "レース数": n,
            "単勝的中率": win / n if n > 0 else 0,
            "複勝的中率": place / n if n > 0 else 0,
            "ランダム単勝": 1 / avg_r if avg_r > 0 else 0,
            "スピアマン相関(中央値)": float(np.median(rhos)) if rhos else float("nan"),
        })
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
    print(f"  単勝的中率   : {m['win_rate']:.1%}  (ランダム: {m['random_win']:.1%}  {m['win_rate']/m['random_win']:.2f}倍)")
    print(f"  複勝的中率   : {m['place_rate']:.1%}  (ランダム: {m['random_place']:.1%}  {m['place_rate']/m['random_place']:.2f}倍)")
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
        print(
            f"  {label:<14}  {s['mean_rho']:>8.4f}  {s['median_rho']:>8.4f}  {s['n']:>8,}"
        )
    print("=" * 65)


def print_breakdown(bdf: pd.DataFrame, title: str) -> None:
    """内訳テーブルをコンソールへ出力する。"""
    print(f"\n  【{title}別内訳】")
    print(f"  {'カテゴリ':<22}  {'レース':>6}  {'単勝%':>7}  {'複勝%':>7}  {'ランダム%':>9}  {'相関ρ':>7}")
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


def save_csv(df: pd.DataFrame, output_path: str) -> None:
    """レース単位の詳細結果をCSVへ出力する。"""
    # 指数1位馬 + 実際の1着馬 を抽出してサマリー行を作成
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    top1["top1_win"] = (top1["finish_position"] == 1).astype(int)
    top1["top1_place"] = (top1["finish_position"] <= 3).astype(int)
    top1 = top1[[
        "race_id", "date", "course_name", "grade", "surface", "distance",
        "head_count", "composite_index", "speed_index", "course_aptitude",
        "jockey_index", "pace_index", "rotation_index", "pedigree_index",
        "finish_position", "top1_win", "top1_place",
    ]]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    top1.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"CSV出力完了: {path} ({len(top1)} 行)")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def run(start_date: str, end_date: str, output_path: str | None, breakdown: str | None) -> None:
    """バックテストを実行する。"""
    logger.info(f"バックテスト開始: {start_date} ～ {end_date}")

    df = load_data(start_date, end_date)
    if df.empty:
        logger.warning("データなし。日付範囲・算出済みデータを確認してください。")
        return

    df = filter_valid_races(df)
    if df.empty:
        logger.warning("有効なレースが見つかりませんでした。")
        return

    metrics = compute_metrics(df)
    print_summary(metrics, start_date, end_date)

    if breakdown:
        bdf = compute_breakdown(df, breakdown)
        titles = {
            "surface": "馬場",
            "distance_cat": "距離カテゴリ",
            "grade": "グレード",
            "course_name": "競馬場",
        }
        print_breakdown(bdf, titles.get(breakdown, breakdown))

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
    args = parser.parse_args()

    for d in (args.start, args.end):
        if len(d) != 8 or not d.isdigit():
            parser.error("日付は YYYYMMDD 形式で指定してください")

    run(args.start, args.end, args.output, args.breakdown)


if __name__ == "__main__":
    main()
