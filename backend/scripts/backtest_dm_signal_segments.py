"""DM シグナル × 条件別 信頼度分析

シグナルが「全体平均」だけ見ると有効でも、特定条件 (場/距離/馬場) では
逆効果になっている可能性がある。条件別の勝率・複勝率・ROI を計測し、
「信頼できる条件」だけシグナル発動するための絞り込み根拠を作る。

使い方:
  .venv/bin/python scripts/backtest_dm_signal_segments.py --start 20230101 --end 20260426
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
logger = logging.getLogger("backtest_dm_segments")


QUERY = text("""
SELECT
    r.id AS race_id, r.date, r.course, r.course_name, r.surface, r.distance, r.grade,
    re.horse_id, re.horse_number,
    ci.composite_index AS base_score,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.finish_position, rr.abnormality_code,
    rr.win_odds, rr.win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start AND :end
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND ci.version = 22
ORDER BY r.date, r.id, re.horse_number
""")


def load(start: str, end: str) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start": start, "end": end}).fetchall()
        cols = ["race_id", "date", "course", "course_name", "surface", "distance", "grade",
                "horse_id", "horse_number", "base_score",
                "jvan_time_dm", "jvan_battle_dm",
                "finish_position", "abnormality_code", "win_odds", "win_popularity"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ["base_score","jvan_time_dm","jvan_battle_dm","finish_position",
              "abnormality_code","win_odds","win_popularity","distance"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    cov = df.groupby("race_id").apply(lambda g: g["jvan_time_dm"].notna().mean(), include_groups=False)
    df = df[df["race_id"].isin(cov[cov >= 0.95].index)].copy()

    rc = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(rc[rc >= 8].index)].copy()

    # ランク付与
    df["base_rank"] = df.groupby("race_id")["base_score"].rank(method="min", ascending=False)
    df["time_rank"] = df.groupby("race_id")["jvan_time_dm"].rank(method="min", ascending=False)
    df["battle_rank"] = df.groupby("race_id")["jvan_battle_dm"].rank(method="min", ascending=False)

    # セグメント分類
    def _surface(s: object) -> str:
        if not isinstance(s, str): return "不明"
        if s.startswith("芝"): return "芝"
        if s.startswith("ダ"): return "ダート"
        if s.startswith("障"): return "障害"
        return s

    def _dist_cat(d: object) -> str:
        if pd.isna(d): return "不明"
        d = float(d)
        if d <= 1400: return "スプリント"
        if d <= 1800: return "マイル"
        if d <= 2400: return "中距離"
        return "長距離"

    df["surface_cat"] = df["surface"].apply(_surface)
    df["dist_cat"] = df["distance"].apply(_dist_cat)
    df["seg"] = df["surface_cat"] + "×" + df["dist_cat"]

    logger.info(f"対象: {df['race_id'].nunique():,} レース / {len(df):,} 馬")
    return df


def metrics(sub: pd.DataFrame) -> dict | None:
    valid = sub[sub["win_odds"].notna() & (sub["win_odds"] > 0)]
    bets = len(valid)
    if bets == 0:
        return None
    wins = (valid["finish_position"] == 1).sum()
    places = (valid["finish_position"] <= 3).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    return {
        "n": int(bets),
        "win_%": round(float(wins / bets * 100), 1),
        "place_%": round(float(places / bets * 100), 1),
        "ROI": round(float(payout / bets * 100), 1),
        "avg_odds": round(float(valid["win_odds"].mean()), 1),
    }


def analyze_signal(df: pd.DataFrame, signal_label: str, mask_fn) -> None:
    """シグナルを場/サーフェイス/距離/seg別に分析"""
    sub = df[mask_fn(df)]
    if sub.empty:
        print(f"\n{signal_label}: データなし")
        return

    overall = metrics(sub)
    print(f"\n{'='*100}")
    print(f"{signal_label}  全体: n={overall['n']} 勝率={overall['win_%']}% 複勝率={overall['place_%']}% ROI={overall['ROI']}% (avg odds={overall['avg_odds']})")
    print(f"{'='*100}")

    # サーフェイス別
    print(f"\n  ── サーフェイス別 ──")
    print(f"  {'surface':>6} {'n':>5} {'勝率':>6} {'複勝':>6} {'ROI':>6}")
    for k, g in sub.groupby("surface_cat"):
        m = metrics(g)
        if m and m["n"] >= 10:
            print(f"  {k:>6} {m['n']:>5} {m['win_%']:>5}% {m['place_%']:>5}% {m['ROI']:>5}%")

    # 距離別
    print(f"\n  ── 距離別 ──")
    print(f"  {'dist':>10} {'n':>5} {'勝率':>6} {'複勝':>6} {'ROI':>6}")
    for k in ["スプリント", "マイル", "中距離", "長距離"]:
        g = sub[sub["dist_cat"] == k]
        m = metrics(g)
        if m and m["n"] >= 10:
            print(f"  {k:>10} {m['n']:>5} {m['win_%']:>5}% {m['place_%']:>5}% {m['ROI']:>5}%")

    # サーフェイス×距離
    print(f"\n  ── サーフェイス×距離 ──")
    print(f"  {'seg':>20} {'n':>5} {'勝率':>6} {'複勝':>6} {'ROI':>6}")
    seg_metrics = []
    for k, g in sub.groupby("seg"):
        m = metrics(g)
        if m and m["n"] >= 20:
            seg_metrics.append((k, m))
    seg_metrics.sort(key=lambda x: x[1]["ROI"], reverse=True)
    for k, m in seg_metrics:
        marker = "✓" if m["ROI"] >= 90 else ("△" if m["ROI"] >= 80 else "✗")
        print(f"  {k:>20} {m['n']:>5} {m['win_%']:>5}% {m['place_%']:>5}% {m['ROI']:>5}% {marker}")

    # 場別
    print(f"\n  ── 場別 ──")
    print(f"  {'course':>6} {'n':>5} {'勝率':>6} {'複勝':>6} {'ROI':>6}")
    course_metrics = []
    for k, g in sub.groupby("course_name"):
        m = metrics(g)
        if m and m["n"] >= 30:
            course_metrics.append((k, m))
    course_metrics.sort(key=lambda x: x[1]["ROI"], reverse=True)
    for k, m in course_metrics:
        marker = "✓" if m["ROI"] >= 90 else ("△" if m["ROI"] >= 80 else "✗")
        print(f"  {k:>6} {m['n']:>5} {m['win_%']:>5}% {m['place_%']:>5}% {m['ROI']:>5}% {marker}")


def run(start: str, end: str) -> None:
    df = load(start, end)
    if df.empty:
        return

    # 軸シグナル
    analyze_signal(df, "🔥 三冠一致 (base=1 ∧ time=1 ∧ battle=1)",
                   lambda d: (d["base_rank"]==1) & (d["time_rank"]==1) & (d["battle_rank"]==1))
    analyze_signal(df, "⭐ 高得点鉄板 (composite≥60 ∧ DM-battle≥65)",
                   lambda d: (d["base_score"]>=60) & (d["jvan_battle_dm"]>=65))

    # 穴シグナル
    analyze_signal(df, "🏆 穴ぐさDM 相当 (DM-battle=1 ∧ 人気≥5) — anagusa は別途",
                   lambda d: (d["battle_rank"]==1) & (d["win_popularity"]>=5))
    analyze_signal(df, "⚡ DM大穴 (battle=1 ∧ 人気≥7 ∧ battle≥65)",
                   lambda d: (d["battle_rank"]==1) & (d["win_popularity"]>=7) & (d["jvan_battle_dm"]>=65))
    analyze_signal(df, "⚡ DM高オッズ (battle=1 ∧ オッズ≥10 ∧ time≤2)",
                   lambda d: (d["battle_rank"]==1) & (d["win_odds"]>=10) & (d["time_rank"]<=2))

    # 警戒
    analyze_signal(df, "❌ 人気下振れ (人気≤3 ∧ base≥4 ∧ battle≥4)",
                   lambda d: (d["win_popularity"]<=3) & (d["base_rank"]>=4) & (d["battle_rank"]>=4))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()
    run(args.start, args.end)


if __name__ == "__main__":
    main()
