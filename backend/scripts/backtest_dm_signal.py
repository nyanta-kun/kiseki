"""DM 指数を「軸シグナル / 穴シグナル」として使う運用パターン検証

合成ウェイトに混ぜるのでなく、DM が特定条件を満たす馬を
「軸候補」「穴候補」としてピックアップする戦略を検証する。

軸シグナル (高信頼度):
  既存総合上位 × DM も上位 = 「複数指標一致の本命」

穴シグナル (妙味):
  既存総合は中位 × DM が突出して高い = 「DM だけが評価する人気薄」

各条件の bets / win_rate / place_rate / tansho_roi を計測。

使い方:
  .venv/bin/python scripts/backtest_dm_signal.py --start 20230101 --end 20260426
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
logger = logging.getLogger("backtest_dm_signal")


QUERY = text("""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    ci.horse_id,
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
WHERE r.date BETWEEN :start_date AND :end_date
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND ci.version = 22
ORDER BY r.date, r.id, ci.horse_id
""")


def load_data(start: str, end: str, min_coverage: float = 0.8) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start_date": start, "end_date": end}).fetchall()
        cols = ["race_id", "date", "horse_id", "base_score",
                "jvan_time_dm", "jvan_battle_dm",
                "finish_position", "abnormality_code", "win_odds", "win_popularity"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    for c in ["base_score", "jvan_time_dm", "jvan_battle_dm",
              "finish_position", "abnormality_code", "win_odds", "win_popularity"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    # 異常コードあり/着順なしのレースを除外
    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    # DMカバレッジフィルタ
    cov = df.groupby("race_id").apply(
        lambda g: g["jvan_time_dm"].notna().mean(), include_groups=False
    )
    keep = cov[cov >= min_coverage].index
    df = df[df["race_id"].isin(keep)].copy()

    # 頭数フィルタ
    rc = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(rc[rc >= 8].index)].copy()  # 8頭以上だけ (3着払い)

    logger.info(
        f"対象: {df['race_id'].nunique():,} レース / {len(df):,} 馬 "
        f"(DM coverage ≥ {min_coverage:.0%}, 頭数 ≥ 8)"
    )
    return df


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """レースごとに各指数のランクを付与する。1位=最良。"""
    df = df.copy()
    df["base_rank"] = df.groupby("race_id")["base_score"].rank(method="min", ascending=False)
    df["time_rank"] = df.groupby("race_id")["jvan_time_dm"].rank(method="min", ascending=False)
    df["battle_rank"] = df.groupby("race_id")["jvan_battle_dm"].rank(method="min", ascending=False)
    # レース内 DM 平均からの偏差
    df["time_dev"] = df["jvan_time_dm"] - df.groupby("race_id")["jvan_time_dm"].transform("mean")
    df["battle_dev"] = df["jvan_battle_dm"] - df.groupby("race_id")["jvan_battle_dm"].transform("mean")
    return df


def evaluate(df_picked: pd.DataFrame, label: str) -> dict:
    """選んだ馬の的中率/ROIを返す。"""
    valid = df_picked[df_picked["win_odds"].notna() & (df_picked["win_odds"] > 0)].copy()
    bets = len(valid)
    if bets == 0:
        return {"label": label, "bets": 0}
    wins = (valid["finish_position"] == 1).sum()
    places = (valid["finish_position"] <= 3).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    avg_odds_hit = (
        valid.loc[valid["finish_position"] == 1, "win_odds"].mean() if wins > 0 else float("nan")
    )
    avg_pop = valid["win_popularity"].mean() if valid["win_popularity"].notna().any() else float("nan")
    return {
        "label": label,
        "bets": int(bets),
        "wins": int(wins),
        "places": int(places),
        "win_rate_%": round(float(wins / bets * 100), 2),
        "place_rate_%": round(float(places / bets * 100), 2),
        "tansho_roi_%": round(float(payout / bets * 100), 2),
        "avg_pop": round(float(avg_pop), 2),
        "avg_odds_hit": round(float(avg_odds_hit), 2) if wins > 0 else float("nan"),
    }


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no data)")
        return
    cols = ["label", "bets", "wins", "win_rate_%", "place_rate_%",
            "tansho_roi_%", "avg_pop", "avg_odds_hit"]
    df = pd.DataFrame(rows)
    df = df[[c for c in cols if c in df.columns]]
    print(df.to_string(index=False))


# ============================================================================
# 軸シグナル パターン
# ============================================================================

def axis_signals(df: pd.DataFrame) -> list[dict]:
    out = []

    # ベースライン: 既存総合1位
    out.append(evaluate(df[df["base_rank"] == 1], "既存総合1位 (基準)"))

    # 既存総合1位 × battle_dm 1位
    out.append(evaluate(
        df[(df["base_rank"] == 1) & (df["battle_rank"] == 1)],
        "総合1位 ∧ DM-battle 1位",
    ))

    # 既存総合1位 × time_dm 1位
    out.append(evaluate(
        df[(df["base_rank"] == 1) & (df["time_rank"] == 1)],
        "総合1位 ∧ DM-time 1位",
    ))

    # 既存総合1位 × DM(両方)1位 = 三冠一致
    out.append(evaluate(
        df[(df["base_rank"] == 1) & (df["time_rank"] == 1) & (df["battle_rank"] == 1)],
        "総合1位 ∧ DM両方 1位 (三冠一致)",
    ))

    # 既存総合1位 × DM(どちらか)1位
    out.append(evaluate(
        df[(df["base_rank"] == 1) & ((df["time_rank"] == 1) | (df["battle_rank"] == 1))],
        "総合1位 ∧ DM(time or battle) 1位",
    ))

    # 既存総合 ≤2 × DM-battle ≤2
    out.append(evaluate(
        df[(df["base_rank"] <= 2) & (df["battle_rank"] <= 2)],
        "総合≤2 ∧ DM-battle ≤2",
    ))

    # 既存総合 ≤3 × DM-battle ≤3 (上位3頭流しの母集団)
    out.append(evaluate(
        df[(df["base_rank"] <= 3) & (df["battle_rank"] <= 3)],
        "総合≤3 ∧ DM-battle ≤3",
    ))

    # 高得点しきい値: base ≥ 60 AND battle_dm ≥ 65
    out.append(evaluate(
        df[(df["base_score"] >= 60) & (df["jvan_battle_dm"] >= 65)],
        "総合≥60 ∧ DM-battle ≥65",
    ))

    return out


# ============================================================================
# 穴シグナル パターン
# ============================================================================

def underdog_signals(df: pd.DataFrame) -> list[dict]:
    out = []

    # ベースライン: 中穴 (人気5-9)
    out.append(evaluate(
        df[df["win_popularity"].between(5, 9)],
        "全馬中穴 (人気5-9, 基準)",
    ))

    # DM-battle で1位 かつ 人気≥5
    out.append(evaluate(
        df[(df["battle_rank"] == 1) & (df["win_popularity"] >= 5)],
        "DM-battle 1位 ∧ 人気≥5 (DM唯一の評価)",
    ))

    # DM-battle で1位 かつ 人気≥7
    out.append(evaluate(
        df[(df["battle_rank"] == 1) & (df["win_popularity"] >= 7)],
        "DM-battle 1位 ∧ 人気≥7",
    ))

    # DM-battle で2位以内 かつ 人気≥6
    out.append(evaluate(
        df[(df["battle_rank"] <= 2) & (df["win_popularity"] >= 6)],
        "DM-battle ≤2 ∧ 人気≥6",
    ))

    # DM-time で1位 かつ 人気≥5
    out.append(evaluate(
        df[(df["time_rank"] == 1) & (df["win_popularity"] >= 5)],
        "DM-time 1位 ∧ 人気≥5",
    ))

    # DM 両方で 1位 かつ 人気≥5 (両DM一致の穴)
    out.append(evaluate(
        df[(df["time_rank"] == 1) & (df["battle_rank"] == 1) & (df["win_popularity"] >= 5)],
        "DM 両方1位 ∧ 人気≥5 (DM両方が穴推奨)",
    ))

    # 既存総合は中位 × DM-battle 1位 (DM だけ高評価)
    out.append(evaluate(
        df[(df["base_rank"] >= 5) & (df["battle_rank"] == 1)],
        "総合≥5位 ∧ DM-battle 1位 (DMだけ評価)",
    ))

    # base_rank と battle_rank の差 N以上 (DMが既存より N位以上高評価)
    out.append(evaluate(
        df[(df["base_rank"] - df["battle_rank"] >= 4) & (df["battle_rank"] <= 3)],
        "DM-battle 既存指数より4位以上上 ∧ DM-battle≤3",
    ))

    # battle_dev (レース内偏差) が大きい × 人気≥5
    out.append(evaluate(
        df[(df["battle_dev"] >= 8) & (df["win_popularity"] >= 5)],
        "battle_dm レース平均比+8以上 ∧ 人気≥5",
    ))

    # battle_dev (レース内偏差) +10以上 (より厳しい突出条件) × 人気≥5
    out.append(evaluate(
        df[(df["battle_dev"] >= 10) & (df["win_popularity"] >= 5)],
        "battle_dm レース平均比+10以上 ∧ 人気≥5",
    ))

    # 高オッズフィルタ: 単勝 ≥10倍 × DM-battle 1位
    out.append(evaluate(
        df[(df["battle_rank"] == 1) & (df["win_odds"] >= 10.0)],
        "単勝≥10倍 ∧ DM-battle 1位",
    ))

    # 単勝 5〜15倍 × DM-battle 1位 (中穴ゾーン)
    out.append(evaluate(
        df[(df["battle_rank"] == 1) & (df["win_odds"].between(5.0, 15.0))],
        "単勝5-15倍 ∧ DM-battle 1位",
    ))

    return out


# ============================================================================
# メイン
# ============================================================================

def run(start: str, end: str, min_coverage: float) -> None:
    df = load_data(start, end, min_coverage)
    if df.empty:
        return
    df = add_ranks(df)

    print("\n" + "=" * 80)
    print(f"DM 軸シグナル検証  期間: {start}〜{end}  カバレッジ≥{min_coverage:.0%}")
    print("=" * 80)
    print_table(axis_signals(df))

    print("\n" + "=" * 80)
    print(f"DM 穴シグナル検証  期間: {start}〜{end}  カバレッジ≥{min_coverage:.0%}")
    print("=" * 80)
    print_table(underdog_signals(df))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--min-coverage", type=float, default=0.8)
    args = p.parse_args()
    run(args.start, args.end, args.min_coverage)


if __name__ == "__main__":
    main()
