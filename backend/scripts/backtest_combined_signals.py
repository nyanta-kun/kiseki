"""穴ぐさ × DM × 指数 の組み合わせシグナル検証

3つの独立情報源を組み合わせて、買うべき穴馬・消すべき人気馬を識別する。

情報源:
  1. 既存総合指数 (base_score, composite_index v22)
  2. JRA-VAN NEXT DM (jvan_time_dm, jvan_battle_dm)
  3. 穴ぐさ (sekito.anagusa rank A/B/C)
  4. 人気・単勝オッズ (race_results)

検証パターン:
  【買うべき穴馬】
    - anagusa A + DM-battle 1位
    - anagusa B以上 + DM 両方1位
    - anagusa A/B + 総合≥5位 + DM-battle 1位
    - anagusa A + battle≥60 + 人気≥5
  【外すべき人気馬】
    - 人気1-3 + 総合≥3位
    - 人気1-3 + DM-battle≥4位 (DM不一致)
    - 人気1 + 三冠不一致
    - 人気1-3 + anagusa pick あり (穴狙い屋が穴と認定)

使い方:
  .venv/bin/python scripts/backtest_combined_signals.py --start 20240101 --end 20260426
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

from src.db.session import sync_engine as engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_combined")


_JRA_TO_SEKITO = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}


QUERY = text("""
WITH base AS (
  SELECT
    r.id AS race_id,
    r.date,
    r.course,
    r.race_number,
    r.surface,
    r.distance,
    re.horse_id,
    re.horse_number,
    ci.composite_index AS base_score,
    re.jvan_time_dm,
    re.jvan_battle_dm,
    rr.finish_position,
    rr.abnormality_code,
    rr.win_odds,
    rr.win_popularity
  FROM keiba.calculated_indices ci
  JOIN keiba.races r ON r.id = ci.race_id
  JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
  JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  WHERE r.date BETWEEN :start AND :end
    AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
    AND ci.version = 22
)
SELECT
  b.*,
  ag.rank AS anagusa_rank
FROM base b
LEFT JOIN sekito.anagusa ag
  ON ag.date = (b.date::text)::date
  AND ag.course_code = CASE b.course
       WHEN '01' THEN 'JSPK' WHEN '02' THEN 'JHKD' WHEN '03' THEN 'JFKS'
       WHEN '04' THEN 'JNGT' WHEN '05' THEN 'JTOK' WHEN '06' THEN 'JNKY'
       WHEN '07' THEN 'JCKO' WHEN '08' THEN 'JKYO' WHEN '09' THEN 'JHSN'
       WHEN '10' THEN 'JKKR' END
  AND ag.race_no = b.race_number
  AND ag.horse_no = b.horse_number
ORDER BY b.date, b.race_id, b.horse_id
""")


def load(start: str, end: str) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start": start, "end": end}).fetchall()
        cols = ["race_id", "date", "course", "race_number", "surface", "distance",
                "horse_id", "horse_number", "base_score",
                "jvan_time_dm", "jvan_battle_dm",
                "finish_position", "abnormality_code", "win_odds", "win_popularity",
                "anagusa_rank"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ["base_score", "jvan_time_dm", "jvan_battle_dm",
              "finish_position", "abnormality_code", "win_odds", "win_popularity",
              "distance"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    cov = df.groupby("race_id").apply(lambda g: g["jvan_time_dm"].notna().mean(), include_groups=False)
    df = df[df["race_id"].isin(cov[cov >= 0.95].index)].copy()

    rc = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(rc[rc >= 8].index)].copy()

    logger.info(f"対象: {df['race_id'].nunique():,} レース / {len(df):,} 馬")
    logger.info(f"anagusa pick あり: {df['anagusa_rank'].notna().sum():,} 馬 "
                f"(A={(df['anagusa_rank']=='A').sum()}, B={(df['anagusa_rank']=='B').sum()}, "
                f"C={(df['anagusa_rank']=='C').sum()})")
    return df


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["base_rank"] = df.groupby("race_id")["base_score"].rank(method="min", ascending=False)
    df["time_rank"] = df.groupby("race_id")["jvan_time_dm"].rank(method="min", ascending=False)
    df["battle_rank"] = df.groupby("race_id")["jvan_battle_dm"].rank(method="min", ascending=False)
    return df


def evaluate(sub: pd.DataFrame, label: str) -> dict | None:
    valid = sub[sub["win_odds"].notna() & (sub["win_odds"] > 0)].copy()
    bets = len(valid)
    if bets == 0:
        return None
    wins = (valid["finish_position"] == 1).sum()
    places = (valid["finish_position"] <= 3).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    payout_p = valid.loc[valid["finish_position"] <= 3, "win_odds"].sum()
    avg_pop = valid["win_popularity"].mean() if valid["win_popularity"].notna().any() else float("nan")
    return {
        "label": label, "bets": int(bets), "wins": int(wins),
        "win_rate_%": round(float(wins / bets * 100), 2),
        "place_rate_%": round(float(places / bets * 100), 2),
        "tansho_roi_%": round(float(payout / bets * 100), 2),
        "avg_pop": round(float(avg_pop), 2) if not np.isnan(avg_pop) else float("nan"),
    }


def print_results(rows: list[dict | None], title: str) -> None:
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)
    rows = [r for r in rows if r is not None]
    if not rows:
        print("(no data)")
        return
    df = pd.DataFrame(rows)
    cols = ["label", "bets", "wins", "win_rate_%", "place_rate_%", "tansho_roi_%", "avg_pop"]
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))


def buy_underdog_signals(df: pd.DataFrame) -> list:
    out = []
    out.append(evaluate(df[df["anagusa_rank"].isin(["A","B","C"])], "anagusa pick全体 (基準)"))
    out.append(evaluate(df[df["anagusa_rank"]=="A"], "anagusa A単独"))
    out.append(evaluate(df[df["anagusa_rank"]=="B"], "anagusa B単独"))
    # 穴ぐさ × DM
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["battle_rank"]==1)], "anagusa A ∧ DM-battle 1位"))
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["time_rank"]==1)], "anagusa A ∧ DM-time 1位"))
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["battle_rank"]==1) & (df["time_rank"]==1)],
                        "anagusa A ∧ DM両方 1位"))
    out.append(evaluate(df[df["anagusa_rank"].isin(["A","B"]) & (df["battle_rank"]==1)],
                        "anagusa A/B ∧ DM-battle 1位"))
    out.append(evaluate(df[df["anagusa_rank"].isin(["A","B"]) & (df["battle_rank"]<=2)],
                        "anagusa A/B ∧ DM-battle ≤2"))
    # 穴ぐさ × DM × 人気
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["battle_rank"]==1) & (df["win_popularity"]>=5)],
                        "anagusa A ∧ DM-battle 1位 ∧ 人気≥5"))
    out.append(evaluate(df[df["anagusa_rank"].isin(["A","B"]) & (df["battle_rank"]==1) & (df["win_popularity"]>=5)],
                        "anagusa A/B ∧ DM-battle 1位 ∧ 人気≥5"))
    # 穴ぐさ × 既存指数
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["base_rank"]<=3)],
                        "anagusa A ∧ 総合≤3位"))
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["base_rank"]>=5)],
                        "anagusa A ∧ 総合≥5位 (隠れ評価)"))
    # フル組み合わせ
    out.append(evaluate(df[(df["anagusa_rank"].isin(["A","B"])) & (df["battle_rank"]==1) & (df["base_rank"]<=3)],
                        "anagusa A/B ∧ DM-battle 1位 ∧ 総合≤3位"))
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["jvan_battle_dm"]>=60) & (df["win_popularity"]>=5)],
                        "anagusa A ∧ DM-battle≥60 ∧ 人気≥5"))
    out.append(evaluate(df[(df["anagusa_rank"]=="A") & (df["jvan_battle_dm"]>=65) & (df["win_popularity"]>=5)],
                        "anagusa A ∧ DM-battle≥65 ∧ 人気≥5"))
    return out


def avoid_popular_signals(df: pd.DataFrame) -> list:
    out = []
    out.append(evaluate(df[df["win_popularity"].between(1,3)], "人気1-3 (基準)"))
    out.append(evaluate(df[df["win_popularity"]==1], "人気1 単勝買い (基準)"))
    # 人気馬の警戒条件
    out.append(evaluate(df[(df["win_popularity"]==1) & (df["base_rank"]>=3)],
                        "人気1 ∧ 総合≥3位 (指数評価低)"))
    out.append(evaluate(df[(df["win_popularity"]==1) & (df["battle_rank"]>=3)],
                        "人気1 ∧ DM-battle ≥3位"))
    out.append(evaluate(df[(df["win_popularity"]==1) & (df["base_rank"]==1) & (df["battle_rank"]>=3)],
                        "人気1 ∧ 総合1位 ∧ DM-battle≥3位 (DM警戒)"))
    out.append(evaluate(df[(df["win_popularity"]<=3) & (df["base_rank"]>=4)],
                        "人気≤3 ∧ 総合≥4位"))
    out.append(evaluate(df[(df["win_popularity"]<=3) & (df["battle_rank"]>=4)],
                        "人気≤3 ∧ DM-battle≥4位"))
    out.append(evaluate(df[(df["win_popularity"]<=3) & (df["base_rank"]>=4) & (df["battle_rank"]>=4)],
                        "人気≤3 ∧ 総合≥4位 ∧ DM-battle≥4位 (両指数警戒)"))
    # 人気馬で穴ぐさが他馬を推している条件
    # → race_id 単位で「自分は人気だが anagusa は別馬を推し」を判定
    pick_races = set(df[df["anagusa_rank"]=="A"]["race_id"])
    out.append(evaluate(df[(df["win_popularity"]<=3) & df["race_id"].isin(pick_races) & df["anagusa_rank"].isna()],
                        "人気≤3 ∧ 自分は穴ぐさpickされず ∧ 他馬がanagusa A"))
    out.append(evaluate(df[(df["win_popularity"]==1) & df["race_id"].isin(pick_races) & df["anagusa_rank"].isna()],
                        "人気1 ∧ 自分は穴ぐさpickされず ∧ 他馬がanagusa A"))
    # 三冠一致しない人気馬
    out.append(evaluate(df[(df["win_popularity"]<=3) & ~((df["base_rank"]==1)&(df["time_rank"]==1)&(df["battle_rank"]==1))],
                        "人気≤3 ∧ 三冠一致でない"))
    return out


def reference_top1_filter(df: pd.DataFrame) -> list:
    """既存総合1位を「DM/anagusa で除外」したときのROI改善を測る"""
    top1 = df[df["base_rank"]==1].copy()
    out = []
    out.append(evaluate(top1, "総合1位 (基準)"))
    # DM-battleが下位なら除外
    out.append(evaluate(top1[top1["battle_rank"]<=2], "総合1位 ∧ DM-battle≤2"))
    out.append(evaluate(top1[top1["battle_rank"]==1], "総合1位 ∧ DM-battle=1"))
    # 三冠一致のみ買う
    out.append(evaluate(top1[(top1["time_rank"]==1) & (top1["battle_rank"]==1)],
                        "総合1位 ∧ 三冠一致"))
    # 穴ぐさが別馬を推している場合は除外
    pick_races = set(df[df["anagusa_rank"]=="A"]["race_id"])
    out.append(evaluate(top1[~top1["race_id"].isin(pick_races) | top1["anagusa_rank"].notna()],
                        "総合1位 ∧ (穴ぐさpickなし OR 自馬がanagusa)"))
    out.append(evaluate(top1[~(top1["race_id"].isin(pick_races) & top1["anagusa_rank"].isna())],
                        "総合1位 ∧ 穴ぐさが他馬Aを推していない"))
    # フル除外
    pick_races_a = set(df[df["anagusa_rank"]=="A"]["race_id"])
    pure = top1[
        (top1["battle_rank"]<=2) &
        ~(top1["race_id"].isin(pick_races_a) & top1["anagusa_rank"].isna())
    ]
    out.append(evaluate(pure, "総合1位 ∧ DM-battle≤2 ∧ 穴ぐさ他馬A推しなし (推奨フィルタ)"))
    return out


def run(start: str, end: str) -> None:
    df = load(start, end)
    if df.empty:
        return
    df = add_ranks(df)

    print_results(buy_underdog_signals(df),
                  f"穴馬ピックアップ (buy underdog) 期間 {start}〜{end}")
    print_results(avoid_popular_signals(df),
                  f"消すべき人気馬 (avoid favorite) 期間 {start}〜{end}")
    print_results(reference_top1_filter(df),
                  f"総合1位フィルタ改善 期間 {start}〜{end}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()
    run(args.start, args.end)


if __name__ == "__main__":
    main()
