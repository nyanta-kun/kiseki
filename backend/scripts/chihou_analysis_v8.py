"""地方競馬 v8指数 多軸検証スクリプト

以下の軸でバックテスト結果を検証する:
  1. 全体サマリ
  2. 信頼度別（指数1位と2位のgap）
  3. 指数信頼度別（有効サブ指数数）
  4. 期待度別（win_probability × win_odds）
  5. 低オッズカット
  6. 競馬場ごと

使い方:
    cd backend
    .venv/bin/python scripts/chihou_analysis_v8.py
    .venv/bin/python scripts/chihou_analysis_v8.py --start 20230416 --end 20240416
    .venv/bin/python scripts/chihou_analysis_v8.py --start 20260413 --end 20260420
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
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
logger = logging.getLogger("chihou_analysis_v8")

engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

# ばんえい競馬は除外
BANEI_COURSE = "83"

# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

BASE_SQL = text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course          AS course_code,
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
    ci.composite_index    AS composite_index,
    ci.win_probability    AS win_probability,
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
  AND ci.version = {CHIHOU_COMPOSITE_VERSION}
  AND r.course != '{BANEI_COURSE}'
ORDER BY r.date, r.id, rr.horse_number
""")


def load_data(start: str, end: str) -> pd.DataFrame:
    sd = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    ed = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    logger.info("データ取得中: %s 〜 %s (v%d)...", start, end, CHIHOU_COMPOSITE_VERSION)
    with Session(engine) as db:
        result = db.execute(BASE_SQL, {"sd": sd, "ed": ed})
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        logger.warning("データなし")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=cols)

    numeric_cols = [
        "speed_index", "last3f_index", "jockey_index", "rotation_index",
        "last_margin_index", "composite_index", "win_probability",
        "finish_position", "win_odds", "win_popularity",
        "head_count", "distance", "abnormality_code",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    logger.info("取得完了: %d 件 / %d レース", len(df), df["race_id"].nunique())
    return df


def filter_valid(df: pd.DataFrame) -> pd.DataFrame:
    """評価に使えないレース・馬を除外する。"""
    df = df[df["abnormality_code"] == 0].copy()
    df = df[df["finish_position"].notna()].copy()
    valid_races = df.groupby("race_id")["horse_id"].count()
    valid_races = valid_races[valid_races >= 4].index
    return df[df["race_id"].isin(valid_races)].copy()


def add_rank(df: pd.DataFrame) -> pd.DataFrame:
    """レース内 composite_index 降順ランクを付与する。"""
    df = df.copy()
    df["pred_rank"] = df.groupby("race_id")["composite_index"].rank(
        method="dense", ascending=False
    )
    return df


# ---------------------------------------------------------------------------
# 指標算出ユーティリティ
# ---------------------------------------------------------------------------

def calc_metrics(top1: pd.DataFrame, label: str = "", n_races: int | None = None) -> dict:
    """top1（指数1位の馬）から各指標を計算する。"""
    if n_races is None:
        n_races = top1["race_id"].nunique()
    valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    bets = len(valid)
    win_cnt = (top1["finish_position"] == 1).sum()
    place_cnt = (top1["finish_position"] <= 3).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    return {
        "label":       label,
        "races":       int(n_races),
        "win_rate":    round(float(win_cnt / n_races * 100) if n_races > 0 else 0.0, 2),
        "place_rate":  round(float(place_cnt / n_races * 100) if n_races > 0 else 0.0, 2),
        "roi_win":     round(float(payout / bets * 100) if bets > 0 else 0.0, 1),
    }


def print_table(title: str, rows: list[dict], cols: list[tuple[str, str, int]]) -> None:
    """汎用テーブル出力。cols = [(key, label, width), ...]"""
    print(f"\n{'=' * 75}")
    print(title)
    print(f"{'=' * 75}")
    header = "".join(f"{lbl:>{w}}" for _, lbl, w in cols)
    print(header)
    print("-" * 75)
    for r in rows:
        if r is None:
            continue
        line = ""
        for key, _, w in cols:
            v = r.get(key, "—")
            if isinstance(v, float):
                line += f"{v:>{w}.1f}"
            elif isinstance(v, int):
                line += f"{v:>{w}}"
            else:
                line += f"{str(v):<{w}}" if key == cols[0][0] else f"{str(v):>{w}}"
        print(line)


METRICS_COLS = [
    ("label",      "条件",       32),
    ("races",      "R数",         6),
    ("win_rate",   "勝率%",       8),
    ("place_rate", "複勝率%",     9),
    ("roi_win",    "ROI%",        8),
]


# ---------------------------------------------------------------------------
# 1. 全体サマリ
# ---------------------------------------------------------------------------

def analyze_overall(df: pd.DataFrame, top1: pd.DataFrame) -> None:
    m = calc_metrics(top1, label="全体")
    print_table(f"■ 1. 全体サマリ（v{CHIHOU_COMPOSITE_VERSION}）", [m], METRICS_COLS)


# ---------------------------------------------------------------------------
# 2. 信頼度別（gap_12: 指数1位と2位の差）
# ---------------------------------------------------------------------------

CONFIDENCE_BINS = [
    ("低（gap<5）",        lambda g: g < 5),
    ("中（5≤gap<10）",    lambda g: (g >= 5) & (g < 10)),
    ("高（10≤gap<15）",   lambda g: (g >= 10) & (g < 15)),
    ("最高（gap≥15）",    lambda g: g >= 15),
]


def analyze_confidence(df: pd.DataFrame, top1: pd.DataFrame) -> None:
    # レース毎のgap_12（1位と2位の指数差）を計算
    def gap_12(g: pd.DataFrame) -> float:
        s = sorted(g["composite_index"].dropna(), reverse=True)
        return s[0] - s[1] if len(s) >= 2 else 0.0

    race_gap = df.groupby("race_id").apply(gap_12)
    top1 = top1.copy()
    top1["gap_12"] = top1["race_id"].map(race_gap)

    rows = []
    for label, cond in CONFIDENCE_BINS:
        sub = top1[cond(top1["gap_12"])]
        if len(sub) < 5:
            rows.append({"label": label, "races": 0, "win_rate": 0, "place_rate": 0, "roi_win": 0})
            continue
        rows.append(calc_metrics(sub, label=label))

    print_table("■ 2. 信頼度別（指数1位と2位のgap）", rows, METRICS_COLS)

    # gap 分布も表示
    print(f"\n  gap分布 (avg={top1['gap_12'].mean():.1f}  "
          f"p25={top1['gap_12'].quantile(0.25):.1f}  "
          f"p50={top1['gap_12'].quantile(0.50):.1f}  "
          f"p75={top1['gap_12'].quantile(0.75):.1f})")


# ---------------------------------------------------------------------------
# 3. 指数信頼度別（有効サブ指数数）
# ---------------------------------------------------------------------------

SUB_INDICES = ["speed_index", "last3f_index", "last_margin_index", "jockey_index", "rotation_index"]
SUB_LABELS  = ["speed", "last3f", "last_margin", "jockey", "rotation"]

RELIABILITY_BINS = [
    ("低（1〜2指数）",  lambda n: n <= 2),
    ("中（3〜4指数）",  lambda n: (n >= 3) & (n <= 4)),
    ("高（全5指数）",   lambda n: n == 5),
]


def analyze_reliability(df: pd.DataFrame, top1: pd.DataFrame) -> None:
    top1 = top1.copy()
    top1["valid_count"] = sum(top1[c].notna() for c in SUB_INDICES)

    rows = []
    for label, cond in RELIABILITY_BINS:
        sub = top1[cond(top1["valid_count"])]
        if len(sub) < 5:
            continue
        rows.append(calc_metrics(sub, label=label))

    print_table("■ 3. 指数信頼度別（有効サブ指数数）", rows, METRICS_COLS)

    # 各サブ指数のnull率
    null_rates = {c: top1[c].isna().mean() * 100 for c in SUB_INDICES}
    print("\n  サブ指数 NULL率:")
    for c, lbl in zip(SUB_INDICES, SUB_LABELS):
        print(f"    {lbl:<15}: {null_rates[c]:.1f}%")


# ---------------------------------------------------------------------------
# 4. 期待度別（EV = win_probability × win_odds）
# ---------------------------------------------------------------------------

EV_BINS = [
    ("EV<0.8（過剰人気）",       lambda ev: ev < 0.8),
    ("EV 0.8〜1.0（やや割高）",  lambda ev: (ev >= 0.8) & (ev < 1.0)),
    ("EV 1.0〜1.5（適正）",      lambda ev: (ev >= 1.0) & (ev < 1.5)),
    ("EV 1.5〜2.0（割安）",      lambda ev: (ev >= 1.5) & (ev < 2.0)),
    ("EV≥2.0（大穴期待）",       lambda ev: ev >= 2.0),
]


def analyze_ev(df: pd.DataFrame, top1: pd.DataFrame) -> None:
    top1 = top1.copy()

    # win_probability が使える場合はそちらを優先
    has_wp = top1["win_probability"].notna() & (top1["win_probability"] > 0)

    # EV1: DB の win_probability を使用
    top1.loc[has_wp, "ev"] = top1.loc[has_wp, "win_probability"] * top1.loc[has_wp, "win_odds"]

    # EV2: win_probability が NULL の場合、composite_index からレース内シェアで推定
    df2 = df.copy()
    race_sum = df2.groupby("race_id")["composite_index"].sum()
    df2["comp_share"] = df2["composite_index"] / df2["race_id"].map(race_sum)
    comp_share_top1 = df2[df2["race_id"].isin(top1["race_id"])]
    top1_share = comp_share_top1.groupby("race_id")["comp_share"].max()
    top1 = top1.join(top1_share.rename("comp_share"), on="race_id")

    no_wp = top1["ev"].isna()
    top1.loc[no_wp, "ev"] = (
        top1.loc[no_wp, "comp_share"] * top1.loc[no_wp, "win_odds"]
    )

    top1_valid = top1[top1["ev"].notna() & top1["win_odds"].notna() & (top1["win_odds"] > 0)]

    rows = []
    for label, cond in EV_BINS:
        sub = top1_valid[cond(top1_valid["ev"])]
        if len(sub) < 5:
            continue
        rows.append(calc_metrics(sub, label=label))

    print_table("■ 4. 期待度別（EV = 推定勝率 × 単勝オッズ）", rows, METRICS_COLS)

    # EV分布
    print(f"\n  EV分布 (avg={top1_valid['ev'].mean():.2f}  "
          f"p25={top1_valid['ev'].quantile(0.25):.2f}  "
          f"p50={top1_valid['ev'].quantile(0.50):.2f}  "
          f"p75={top1_valid['ev'].quantile(0.75):.2f})")
    print(f"  win_probability 使用率: {has_wp.mean()*100:.1f}%")


# ---------------------------------------------------------------------------
# 5. 低オッズカット
# ---------------------------------------------------------------------------

ODDS_THRESHOLDS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0]


def analyze_odds_cut(top1: pd.DataFrame) -> None:
    top1_valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)].copy()
    total_all = len(top1_valid)

    rows = []
    for thr in ODDS_THRESHOLDS:
        sub = top1_valid[top1_valid["win_odds"] >= thr]
        n = len(sub)
        if n < 5:
            break
        skipped = total_all - n
        wins = (sub["finish_position"] == 1).sum()
        places = (sub["finish_position"] <= 3).sum()
        payout = sub.loc[sub["finish_position"] == 1, "win_odds"].sum()
        roi = float(payout / n * 100) if n > 0 else 0.0
        rows.append({
            "label":     f"odds≥{thr}",
            "races":     n,
            "skipped":   skipped,
            "skip_pct":  round(skipped / total_all * 100, 1) if total_all > 0 else 0.0,
            "win_rate":  round(wins / n * 100, 2) if n > 0 else 0.0,
            "place_rate": round(places / n * 100, 2) if n > 0 else 0.0,
            "roi_win":   round(roi, 1),
        })

    cols = [
        ("label",      "カット閾値",   14),
        ("races",      "対象R",         7),
        ("skipped",    "除外R",         7),
        ("skip_pct",   "除外%",         7),
        ("win_rate",   "勝率%",         8),
        ("place_rate", "複勝率%",       9),
        ("roi_win",    "ROI%",          8),
    ]
    print_table("■ 5. 低オッズカット（指数1位の単勝オッズ下限）", rows, cols)


# ---------------------------------------------------------------------------
# 6. 競馬場ごと
# ---------------------------------------------------------------------------

def analyze_by_course(top1: pd.DataFrame) -> None:
    rows = []
    for course, grp in top1.groupby("course_name"):
        total = len(grp)
        if total < 10:
            continue
        wins = (grp["finish_position"] == 1).sum()
        places = (grp["finish_position"] <= 3).sum()
        valid = grp[grp["win_odds"].notna() & (grp["win_odds"] > 0)]
        bets = len(valid)
        payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
        roi = float(payout / bets * 100) if bets > 0 else 0.0
        rows.append({
            "label":      course,
            "races":      total,
            "win_rate":   round(wins / total * 100, 1),
            "place_rate": round(places / total * 100, 1),
            "roi_win":    round(roi, 1),
        })

    rows.sort(key=lambda r: r["roi_win"], reverse=True)
    cols = [
        ("label",      "競馬場",    10),
        ("races",      "R数",        6),
        ("win_rate",   "勝率%",      8),
        ("place_rate", "複勝率%",    9),
        ("roi_win",    "ROI%",       8),
    ]
    print_table("■ 6. 競馬場ごと（ROI降順）", rows, cols)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="地方競馬 v8指数 多軸検証")
    parser.add_argument("--start", default="20230416", help="開始日 YYYYMMDD")
    parser.add_argument("--end",   default="20240416", help="終了日 YYYYMMDD")
    args = parser.parse_args()

    df = load_data(args.start, args.end)
    if df.empty:
        print("データなし")
        return

    df = filter_valid(df)
    df = add_rank(df)

    top1 = df[df["pred_rank"] == 1].copy()

    print(f"\n期間: {args.start} 〜 {args.end}  v{CHIHOU_COMPOSITE_VERSION}")
    print(f"有効レース: {df['race_id'].nunique()}  有効馬: {len(df)}")

    analyze_overall(df, top1)
    analyze_confidence(df, top1)
    analyze_reliability(df, top1)
    analyze_ev(df, top1)
    analyze_odds_cut(top1)
    analyze_by_course(top1)


if __name__ == "__main__":
    main()
