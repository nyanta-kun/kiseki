"""地方競馬 調教師関連バックテスト分析

以下3つの軸で分析する:
  1. 調教師×騎手コンボ  : (trainer_id, jockey_id)ペアの勝率・ROI
  2. 調教師×レース間隔  : (trainer_id, interval_bucket)の勝率・ROI
  3. 人気薄激走パターン : ability_baseが高いのにオッズが高い馬の実績
                         + 騎手変更時の勝率変化

使い方:
    cd backend
    uv run python scripts/chihou_trainer_analysis.py
    uv run python scripts/chihou_trainer_analysis.py --start 20230416 --end 20260416
    uv run python scripts/chihou_trainer_analysis.py --mode combo     # コンボのみ
    uv run python scripts/chihou_trainer_analysis.py --mode interval  # 間隔のみ
    uv run python scripts/chihou_trainer_analysis.py --mode dark      # 人気薄のみ
    uv run python scripts/chihou_trainer_analysis.py --top 30         # 上位30コンボ表示
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# DB接続（同期）
# -----------------------------------------------------------------------
def get_engine():
    return create_engine(settings.database_url_sync, pool_pre_ping=True, echo=False)


# -----------------------------------------------------------------------
# データ取得
# -----------------------------------------------------------------------
CHIHOU_VERSION = 7  # バックフィル済みバージョン

QUERY = f"""
SELECT
    r.date,
    r.course_name,
    r.distance,
    e.horse_id,
    e.trainer_id,
    t.name                                AS trainer_name,
    e.jockey_id,
    j.name                                AS jockey_name,
    e.prev_jockey_code,
    res.finish_position,
    res.abnormality_code,
    res.win_odds,
    res.win_popularity,
    -- 前走間隔: 同馬の直前レース日との差
    (r.date::date - LAG(r.date::date) OVER (
        PARTITION BY e.horse_id ORDER BY r.date
    ))                                    AS interval_days,
    -- 前走騎手ID（騎手変更検出用）
    LAG(e.jockey_id) OVER (
        PARTITION BY e.horse_id ORDER BY r.date
    )                                     AS prev_jockey_id,
    -- 指数（v7 composite）
    ci.composite_index,
    ci.speed_index,
    -- レース内での指数順位
    RANK() OVER (
        PARTITION BY r.id
        ORDER BY ci.composite_index DESC NULLS LAST
    )                                     AS index_rank
FROM chihou.races r
JOIN chihou.race_entries e   ON e.race_id = r.id
JOIN chihou.race_results res ON res.race_id = r.id AND res.horse_id = e.horse_id
LEFT JOIN chihou.jockeys  j  ON j.id = e.jockey_id
LEFT JOIN chihou.trainers t  ON t.id = e.trainer_id
LEFT JOIN chihou.calculated_indices ci
    ON ci.race_id = r.id AND ci.horse_id = e.horse_id
    AND ci.version = {CHIHOU_VERSION}
WHERE r.date >= :start
  AND r.date <= :end
  AND res.finish_position IS NOT NULL
  AND res.abnormality_code = 0
  AND r.course != '83'
ORDER BY r.date, r.id, e.horse_number
"""


def load_data(start: str, end: str) -> pd.DataFrame:
    engine = get_engine()
    sd = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    ed = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    logger.info("データ取得中: %s 〜 %s", start, end)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        result = db.execute(text(QUERY), {"start": sd, "end": ed})
        rows = result.fetchall()
        cols = list(result.keys())
    df = pd.DataFrame(rows, columns=cols)
    # Decimal型 → float 変換
    for col in ["win_odds", "composite_index", "speed_index"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["finish_position", "interval_days", "index_rank", "jockey_id", "trainer_id", "prev_jockey_id"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    logger.info("取得: %d 件 / %d ユニークレース", len(df), df[["date", "course_name"]].drop_duplicates().shape[0])
    return df


# -----------------------------------------------------------------------
# 1. 調教師×騎手コンボ分析
# -----------------------------------------------------------------------
def analyze_trainer_jockey_combo(df: pd.DataFrame, min_rides: int = 20, top_n: int = 30) -> None:
    print("\n" + "=" * 80)
    print("1. 調教師×騎手コンボ分析")
    print("=" * 80)

    sub = df[df["trainer_id"].notna() & df["jockey_id"].notna()].copy()
    sub["win"] = (sub["finish_position"] == 1).astype(int)
    sub["place"] = (sub["finish_position"] <= 3).astype(int)
    sub["roi_contrib"] = sub.apply(
        lambda x: x["win_odds"] if (x["finish_position"] == 1 and x["win_odds"] is not None) else 0,
        axis=1
    )

    # 全体ベースライン
    baseline_win  = sub["win"].mean()
    baseline_place = sub["place"].mean()

    # コンボ集計
    grp = sub.groupby(["trainer_name", "jockey_name"]).agg(
        rides      = ("win", "count"),
        wins       = ("win", "sum"),
        places     = ("place", "sum"),
        roi_sum    = ("roi_contrib", "sum"),
    ).reset_index()
    grp = grp[grp["rides"] >= min_rides].copy()
    grp["win_pct"]   = grp["wins"] / grp["rides"] * 100
    grp["place_pct"] = grp["places"] / grp["rides"] * 100
    grp["roi"]       = grp["roi_sum"] / grp["rides"] * 100
    grp["win_lift"]  = grp["win_pct"] - baseline_win * 100

    print(f"\nベースライン: 勝率={baseline_win*100:.1f}%  複勝率={baseline_place*100:.1f}%")
    print(f"最低騎乗数: {min_rides}回以上 / 対象コンボ数: {len(grp)}")

    # ROI上位
    top_roi = grp.sort_values("roi", ascending=False).head(top_n)
    print(f"\n--- ROI上位{top_n}コンボ ---")
    print(f"{'調教師':<15} {'騎手':<12} {'騎乗':>5} {'勝率':>7} {'複勝率':>7} {'ROI':>8} {'勝率差':>8}")
    print("-" * 70)
    for _, row in top_roi.iterrows():
        print(f"{row['trainer_name']:<15} {row['jockey_name']:<12} "
              f"{row['rides']:>5} {row['win_pct']:>6.1f}% {row['place_pct']:>6.1f}% "
              f"{row['roi']:>7.1f}% {row['win_lift']:>+7.1f}%")

    # 勝率lift上位（ROIに寄らず勝率が高いコンボ）
    top_lift = grp.sort_values("win_lift", ascending=False).head(top_n)
    print(f"\n--- 勝率リフト上位{top_n}コンボ（vs ベースライン） ---")
    print(f"{'調教師':<15} {'騎手':<12} {'騎乗':>5} {'勝率':>7} {'複勝率':>7} {'ROI':>8} {'勝率差':>8}")
    print("-" * 70)
    for _, row in top_lift.iterrows():
        print(f"{row['trainer_name']:<15} {row['jockey_name']:<12} "
              f"{row['rides']:>5} {row['win_pct']:>6.1f}% {row['place_pct']:>6.1f}% "
              f"{row['roi']:>7.1f}% {row['win_lift']:>+7.1f}%")

    # コース別コンボ集計（上位コース）
    print("\n--- コース別コンボ効果サマリ ---")
    course_grp = sub.groupby("course_name").agg(
        rides     = ("win", "count"),
        win_rate  = ("win", "mean"),
    ).reset_index()
    # コンボ別課題: コースごとにコンボ効果が出ているか
    combo_course = sub.groupby(["course_name", "trainer_name", "jockey_name"]).agg(
        rides = ("win", "count"),
        wins  = ("win", "sum"),
        roi_sum = ("roi_contrib", "sum"),
    ).reset_index()
    combo_course = combo_course[combo_course["rides"] >= 10]
    combo_course["win_pct"] = combo_course["wins"] / combo_course["rides"] * 100
    combo_course["roi"]     = combo_course["roi_sum"] / combo_course["rides"] * 100

    course_summary = combo_course.groupby("course_name").agg(
        combos       = ("trainer_name", "count"),
        avg_win_pct  = ("win_pct", "mean"),
        avg_roi      = ("roi", "mean"),
        best_roi     = ("roi", "max"),
    ).reset_index().sort_values("avg_roi", ascending=False)
    print(f"{'コース':<10} {'コンボ数':>8} {'平均勝率':>8} {'平均ROI':>9} {'最高ROI':>9}")
    print("-" * 50)
    for _, row in course_summary.iterrows():
        print(f"{row['course_name']:<10} {row['combos']:>8} "
              f"{row['avg_win_pct']:>7.1f}% {row['avg_roi']:>8.1f}% {row['best_roi']:>8.1f}%")


# -----------------------------------------------------------------------
# 2. 調教師×レース間隔分析
# -----------------------------------------------------------------------
INTERVAL_BUCKETS = [
    (0,  7,  "中3日以内"),
    (8, 14,  "中1〜2週"),
    (15, 21, "中3週"),
    (22, 35, "中4〜5週"),
    (36, 56, "中6〜8週"),
    (57, 120,"中9〜17週"),
    (121, 9999, "長期休養明け"),
]

def interval_bucket(days) -> str:
    if days is None or pd.isna(days):
        return "初戦/不明"
    d = int(days)
    for lo, hi, label in INTERVAL_BUCKETS:
        if lo <= d <= hi:
            return label
    return "長期休養明け"


def analyze_trainer_interval(df: pd.DataFrame, min_samples: int = 15, top_n: int = 25) -> None:
    print("\n" + "=" * 80)
    print("2. 調教師×レース間隔分析")
    print("=" * 80)

    sub = df[df["trainer_id"].notna()].copy()
    sub["win"] = (sub["finish_position"] == 1).astype(int)
    sub["place"] = (sub["finish_position"] <= 3).astype(int)
    sub["roi_contrib"] = sub.apply(
        lambda x: x["win_odds"] if (x["finish_position"] == 1 and x["win_odds"] is not None) else 0,
        axis=1
    )
    sub["interval_bucket"] = sub["interval_days"].apply(interval_bucket)

    # 全体：間隔別ベースライン
    print("\n--- 間隔別 全体ベースライン ---")
    bl = sub.groupby("interval_bucket").agg(
        count    = ("win", "count"),
        win_pct  = ("win", "mean"),
        place_pct= ("place", "mean"),
        roi      = ("roi_contrib", lambda x: x.sum() / len(x) * 100),
    ).reset_index()
    # 順序ソート
    order = [b[2] for b in INTERVAL_BUCKETS] + ["初戦/不明"]
    bl["_ord"] = bl["interval_bucket"].map({v: i for i, v in enumerate(order)})
    bl = bl.sort_values("_ord")
    print(f"{'間隔区分':<14} {'件数':>7} {'勝率':>7} {'複勝率':>7} {'ROI':>8}")
    print("-" * 50)
    for _, row in bl.iterrows():
        print(f"{row['interval_bucket']:<14} {row['count']:>7} "
              f"{row['win_pct']*100:>6.1f}% {row['place_pct']*100:>6.1f}% {row['roi']:>7.1f}%")

    # 調教師×間隔コンボ
    grp = sub.groupby(["trainer_name", "interval_bucket"]).agg(
        rides     = ("win", "count"),
        wins      = ("win", "sum"),
        places    = ("place", "sum"),
        roi_sum   = ("roi_contrib", "sum"),
    ).reset_index()
    grp = grp[grp["rides"] >= min_samples].copy()
    grp["win_pct"]   = grp["wins"] / grp["rides"] * 100
    grp["place_pct"] = grp["places"] / grp["rides"] * 100
    grp["roi"]       = grp["roi_sum"] / grp["rides"] * 100

    # 間隔別ベースラインとの差
    bl_dict = bl.set_index("interval_bucket")["win_pct"].to_dict()
    grp["win_lift"] = grp.apply(
        lambda x: x["win_pct"] / 100 - bl_dict.get(x["interval_bucket"], 0), axis=1
    ) * 100

    # ROI上位コンボ
    top = grp.sort_values("roi", ascending=False).head(top_n)
    print(f"\n--- 調教師×間隔 ROI上位{top_n}コンボ（最低{min_samples}回以上）---")
    print(f"{'調教師':<15} {'間隔':<14} {'回数':>5} {'勝率':>7} {'ROI':>8} {'勝率差':>8}")
    print("-" * 65)
    for _, row in top.iterrows():
        print(f"{row['trainer_name']:<15} {row['interval_bucket']:<14} "
              f"{row['rides']:>5} {row['win_pct']:>6.1f}% "
              f"{row['roi']:>7.1f}% {row['win_lift']:>+7.1f}%")

    # 「ため使い」パターン（長期休養明けのROI上位）
    rest = grp[grp["interval_bucket"].isin(["中9〜17週", "長期休養明け"])].sort_values("roi", ascending=False).head(15)
    if not rest.empty:
        print(f"\n--- 長期休養明けROI上位（中9週以上）---")
        print(f"{'調教師':<15} {'間隔':<14} {'回数':>5} {'勝率':>7} {'ROI':>8}")
        print("-" * 55)
        for _, row in rest.iterrows():
            print(f"{row['trainer_name']:<15} {row['interval_bucket']:<14} "
                  f"{row['rides']:>5} {row['win_pct']:>6.1f}% {row['roi']:>7.1f}%")


# -----------------------------------------------------------------------
# 3. 人気薄激走パターン分析
# -----------------------------------------------------------------------
def analyze_dark_horse(df: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("3. 人気薄激走パターン分析")
    print("=" * 80)

    sub = df[df["win_odds"].notna() & df["composite_index"].notna()].copy()
    sub["win"] = (sub["finish_position"] == 1).astype(int)
    sub["place"] = (sub["finish_position"] <= 3).astype(int)
    sub["roi_contrib"] = sub.apply(
        lambda x: x["win_odds"] if x["win"] else 0, axis=1
    )
    # 騎手変更フラグ: jockey_id vs 前走jockey_id
    sub["jockey_changed"] = (
        sub["prev_jockey_id"].notna() &
        sub["jockey_id"].notna() &
        (sub["jockey_id"] != sub["prev_jockey_id"])
    )

    # 3-A: 指数順位×オッズ帯のクロス集計
    print("\n--- 3-A: 指数順位 × オッズ帯 （勝率・ROI）---")
    sub["odds_bucket"] = pd.cut(
        sub["win_odds"],
        bins=[0, 2.0, 3.9, 5.9, 9.9, 19.9, 9999],
        labels=["〜2倍", "2〜4倍", "4〜6倍", "6〜10倍", "10〜20倍", "20倍超"],
    )
    cross = sub[sub["index_rank"] <= 4].groupby(["index_rank", "odds_bucket"], observed=True).agg(
        count    = ("win", "count"),
        win_pct  = ("win", "mean"),
        place_pct= ("place", "mean"),
        roi      = ("roi_contrib", lambda x: x.sum() / len(x) * 100),
    ).reset_index()
    print(f"{'指数順位':>6} {'オッズ帯':<10} {'件数':>6} {'勝率':>7} {'複勝率':>7} {'ROI':>8}")
    print("-" * 55)
    for _, row in cross.iterrows():
        print(f"{row['index_rank']:>6} {str(row['odds_bucket']):<10} {row['count']:>6} "
              f"{row['win_pct']*100:>6.1f}% {row['place_pct']*100:>6.1f}% {row['roi']:>7.1f}%")

    # 3-B: 「指数1位 × 5倍以上」= 市場が見落とした馬
    dark = sub[(sub["index_rank"] == 1) & (sub["win_odds"] >= 5.0)].copy()
    print(f"\n--- 3-B: 指数1位 × 単勝5倍以上（n={len(dark)}）---")
    if not dark.empty:
        print(f"  全体: 勝率={dark['win'].mean()*100:.1f}%  複勝率={dark['place'].mean()*100:.1f}%  "
              f"ROI={dark['roi_contrib'].sum()/len(dark)*100:.1f}%")

        # コース別
        dark_course = dark.groupby("course_name").agg(
            count    = ("win", "count"),
            win_pct  = ("win", "mean"),
            place_pct= ("place", "mean"),
            roi      = ("roi_contrib", lambda x: x.sum() / len(x) * 100),
        ).reset_index().sort_values("roi", ascending=False)
        print(f"\n  コース別:")
        print(f"  {'コース':<10} {'件数':>6} {'勝率':>7} {'複勝率':>7} {'ROI':>8}")
        print("  " + "-" * 45)
        for _, row in dark_course.iterrows():
            print(f"  {row['course_name']:<10} {row['count']:>6} "
                  f"{row['win_pct']*100:>6.1f}% {row['place_pct']*100:>6.1f}% {row['roi']:>7.1f}%")

        # オッズ別
        dark_odds = dark.groupby("odds_bucket", observed=True).agg(
            count    = ("win", "count"),
            win_pct  = ("win", "mean"),
            place_pct= ("place", "mean"),
            roi      = ("roi_contrib", lambda x: x.sum() / len(x) * 100),
        ).reset_index()
        print(f"\n  オッズ帯別:")
        print(f"  {'オッズ帯':<10} {'件数':>6} {'勝率':>7} {'複勝率':>7} {'ROI':>8}")
        print("  " + "-" * 45)
        for _, row in dark_odds.iterrows():
            print(f"  {str(row['odds_bucket']):<10} {row['count']:>6} "
                  f"{row['win_pct']*100:>6.1f}% {row['place_pct']*100:>6.1f}% {row['roi']:>7.1f}%")

    # 3-C: 騎手変更時の勝率変化
    print(f"\n--- 3-C: 騎手変更の影響 ---")
    changed     = sub[sub["jockey_changed"] == True]
    not_changed = sub[sub["jockey_changed"] == False]
    print(f"  変更あり (n={len(changed)}): 勝率={changed['win'].mean()*100:.1f}%  "
          f"ROI={changed['roi_contrib'].sum()/max(len(changed),1)*100:.1f}%")
    print(f"  変更なし (n={len(not_changed)}): 勝率={not_changed['win'].mean()*100:.1f}%  "
          f"ROI={not_changed['roi_contrib'].sum()/max(len(not_changed),1)*100:.1f}%")

    # 指数1位 × 騎手変更
    dark_changed = dark[dark["jockey_changed"] == True]
    dark_nc      = dark[dark["jockey_changed"] == False]
    if not dark_changed.empty:
        print(f"\n  指数1位×5倍以上 × 騎手変更あり (n={len(dark_changed)}): "
              f"勝率={dark_changed['win'].mean()*100:.1f}%  "
              f"ROI={dark_changed['roi_contrib'].sum()/len(dark_changed)*100:.1f}%")
    if not dark_nc.empty:
        print(f"  指数1位×5倍以上 × 騎手変更なし (n={len(dark_nc)}): "
              f"勝率={dark_nc['win'].mean()*100:.1f}%  "
              f"ROI={dark_nc['roi_contrib'].sum()/len(dark_nc)*100:.1f}%")

    # 3-D: 前走異常（除外・中止等）後の激走
    # abnormality_codeを前走から引き継ぐには別途LAGが必要なので、
    # ここでは「前走着順が悪い（6着以下）× 指数1位 × 5倍以上」で近似
    print(f"\n--- 3-D: 指数1位 × 5倍以上 × 前走間隔別 ---")
    dark["interval_bucket"] = dark["interval_days"].apply(interval_bucket)
    dark_interval = dark.groupby("interval_bucket").agg(
        count    = ("win", "count"),
        win_pct  = ("win", "mean"),
        place_pct= ("place", "mean"),
        roi      = ("roi_contrib", lambda x: x.sum() / len(x) * 100),
    ).reset_index()
    order = [b[2] for b in INTERVAL_BUCKETS] + ["初戦/不明"]
    dark_interval["_ord"] = dark_interval["interval_bucket"].map({v: i for i, v in enumerate(order)})
    dark_interval = dark_interval.sort_values("_ord")
    print(f"  {'間隔区分':<14} {'件数':>6} {'勝率':>7} {'複勝率':>7} {'ROI':>8}")
    print("  " + "-" * 50)
    for _, row in dark_interval.iterrows():
        print(f"  {row['interval_bucket']:<14} {row['count']:>6} "
              f"{row['win_pct']*100:>6.1f}% {row['place_pct']*100:>6.1f}% {row['roi']:>7.1f}%")


# -----------------------------------------------------------------------
# main
# -----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="地方競馬 調教師関連バックテスト分析")
    parser.add_argument("--start",  default="20230416", help="開始日 YYYYMMDD")
    parser.add_argument("--end",    default="20260416", help="終了日 YYYYMMDD")
    parser.add_argument("--mode",   default="all",
                        choices=["all", "combo", "interval", "dark"],
                        help="分析モード (default: all)")
    parser.add_argument("--top",    type=int, default=30, help="上位N件表示 (default: 30)")
    parser.add_argument("--min-rides", type=int, default=20, help="コンボ最低騎乗数 (default: 20)")
    args = parser.parse_args()

    df = load_data(args.start, args.end)
    if df.empty:
        print("データが取得できませんでした")
        return

    if args.mode in ("all", "combo"):
        analyze_trainer_jockey_combo(df, min_rides=args.min_rides, top_n=args.top)

    if args.mode in ("all", "interval"):
        analyze_trainer_interval(df, min_samples=args.min_rides, top_n=args.top)

    if args.mode in ("all", "dark"):
        analyze_dark_horse(df)


if __name__ == "__main__":
    main()
