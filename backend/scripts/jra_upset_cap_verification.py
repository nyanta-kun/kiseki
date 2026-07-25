"""JRA 穴badge「複勝圏頭数キャップ」検証

ユーザー要望(2026-07-25): 1レースに「買うべき」ラベルが多数付くと購入判断の
役に立たない。複勝圏の実払戻頭数(8頭以上=3着まで、7頭以下=2着まで、JRA/NAR
共通ルール)に合わせて badge 該当馬をレースあたり K=2/3 頭までに絞り、
badge_cnt降順(タイは composite_index 降順)で上位K頭のみに穴badgeを付与する
案を検証する。

検証目的: 上位K頭に絞った badge_position(1位, 2位, [3位]) の複勝的中率を
求め、K頭合算(position1+position2[+3])が100%以上になるか(=平均して
レースあたり1頭以上は複勝圏に来ると期待できる、という粗いカバレッジ基準)。

使い方:
  cd backend
  .venv/bin/python scripts/jra_upset_cap_verification.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

sys.path.insert(0, str(_here.parent))
from jra_axis_segment_deny_analysis import fetch_base  # noqa: E402
from jra_verify_signals import annotate, fetch_external  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_upset_cap")

UPSET_MIN_ODDS = 10.0


def _roi_row(sub: pd.DataFrame, rng: np.random.Generator, n_boot: int = 2000) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "win": 0.0, "plc": 0.0, "lo": 0.0, "hi": 0.0}
    fp = sub["finish_position"].to_numpy()
    win = fp == 1
    plc = fp <= 3
    boot = [rng.choice(plc.astype(float), size=n, replace=True).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n": n, "win": win.mean() * 100, "plc": plc.mean() * 100, "lo": lo * 100, "hi": hi * 100}


def build_capped_badges(df: pd.DataFrame, min_badge: int = 1) -> pd.DataFrame:
    """レースごとに badge_cnt を計算し、複勝圏頭数(K=2/3)でキャップして上位K頭のみ残す。"""
    df = df.copy()

    def _dm_battle_rank(g: pd.DataFrame) -> pd.Series:
        d = g["jvan_battle_dm"]
        if d.notna().sum() < 2:
            return pd.Series([np.nan] * len(g), index=g.index)
        return d.rank(method="min", ascending=False)
    df["dm_battle_rank"] = df.groupby("race_id", group_keys=False).apply(_dm_battle_rank)

    def _badge_cnt(row) -> int:
        b = 0
        if row["anagusa_rank"] in ("A", "B", "C"):
            b += 1
        if pd.notna(row["nb_ave_rank"]) and row["nb_ave_rank"] <= 3:
            b += 1
        if pd.notna(row["km_rank"]) and row["km_rank"] <= 3:
            b += 1
        if pd.notna(row["dm_battle_rank"]) and row["dm_battle_rank"] <= 2:
            b += 1
        return b

    unpop = df[df["win_odds"] >= UPSET_MIN_ODDS].copy()
    unpop["badge_cnt"] = unpop.apply(_badge_cnt, axis=1)
    unpop = unpop[unpop["badge_cnt"] >= min_badge].copy()

    parts = []
    for rid, g in df.groupby("race_id", sort=False):
        head_count = len(g)  # 出走取消等を annotate 前に除いた実質頭数
        k = 3 if head_count >= 8 else 2
        cand = unpop[unpop["race_id"] == rid].copy()
        if cand.empty:
            continue
        cand = cand.sort_values(["badge_cnt", "composite_index"], ascending=[False, False])
        cand = cand.head(k).reset_index(drop=True)
        cand["badge_position"] = np.arange(1, len(cand) + 1)
        cand["k_group"] = k
        parts.append(cand)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--end", default="20260725")
    p.add_argument("--min-badge", type=int, default=1)
    args = p.parse_args()

    rng = np.random.default_rng(12345)
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch_base(conn, args.start, args.end)
    ext = fetch_external(conn, args.start, args.end)
    conn.close()

    logger.info("シグナル付与中...")
    df = annotate(df, ext)
    df["date"] = df["date"].astype(str)

    capped = build_capped_badges(df, min_badge=args.min_badge)
    logger.info("キャップ後: %d行 (元 win_odds>=10 badge_cnt>=1 該当馬から絞り込み)", len(capped))

    train = capped[capped["date"] < args.train_end]
    test = capped[capped["date"] >= args.test_start]

    print("\n" + "#" * 92)
    print("# JRA 穴badge 複勝圏頭数キャップ検証（レースあたり badge_cnt上位K頭のみ表示・K=2/3）")
    print("#" * 92)

    for label, d in (("train+val", train), ("test(OOS)", test)):
        print(f"\n{'='*30} {label} {'='*30}")
        for k in (2, 3):
            dk = d[d["k_group"] == k]
            print(f"\n--- K={k} (頭数{'8+' if k == 3 else '-7'}のレース、n_races={dk['race_id'].nunique()}) ---")
            print(f"  {'badge_position':<16}{'n':>7}{'単勝的中':>10}{'複勝的中':>10}{'95%CI':>16}")
            cum = 0.0
            for pos in range(1, k + 1):
                sub = dk[dk["badge_position"] == pos]
                st = _roi_row(sub, rng)
                cum += st["plc"]
                print(f"  {pos:<16}{st['n']:>7}{st['win']:>9.1f}%{st['plc']:>9.1f}%"
                      f"  [{st['lo']:.1f},{st['hi']:.1f}]")
            mark = "★合算100%達成" if cum >= 100.0 else "▼合算100%未達"
            print(f"  → 合算複勝的中率(position1..{k}の単純和) = {cum:.1f}%  {mark}")


if __name__ == "__main__":
    main()
