"""JRA「穴」タグ(K=1本番選定と同一ロジック)該当馬のROIセグメント分析

ユーザー要望(2026-07-25): 本日「穴」タグの好走をいくつか確認できたため、
条件を絞ることで単勝/複勝ROI≥100%を狙えるセグメントがないか検証する。

母集団の再現方法: dm_signals.py の compute_dm_signals() と同一ロジック
(badge_cnt = 穴ぐさ/netkeiba/kichiuma/DM-battleの一致数、単勝オッズ≥10、
レースにつきbadge_cnt最大の1頭のみ・同点はcomposite_index降順)で「穴」該当馬
を3年分再構築し、複数のセグメント(場・馬場・距離帯・頭数帯・オッズ帯・
badge_cnt・指数順位)で単勝/複勝ROIを train+val/testの2窓で検証する。

使い方:
  cd backend
  .venv/bin/python scripts/jra_upset_candidate_roi_segment.py
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
logger = logging.getLogger("jra_upset_candidate_roi")

UPSET_MIN_ODDS = 10.0


def build_upset_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """本番 compute_dm_signals と同一ロジックで「穴」該当馬(レース1頭)を再構築する。"""
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
    unpop = unpop[unpop["badge_cnt"] >= 1].copy()

    parts = []
    for rid, g in unpop.groupby("race_id", sort=False):
        g = g.sort_values(["badge_cnt", "composite_index"], ascending=[False, False])
        parts.append(g.head(1))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _roi_row(sub: pd.DataFrame, rng: np.random.Generator, n_boot: int = 2000) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "win": 0.0, "plc": 0.0, "win_roi": 0.0, "plc_roi": 0.0,
                "win_lo": 0.0, "win_hi": 0.0, "plc_lo": 0.0, "plc_hi": 0.0}
    fp = sub["finish_position"].to_numpy()
    win_odds = sub["win_odds"].to_numpy()
    plc_odds = sub["place_odds"].to_numpy()
    win = fp == 1
    plc = fp <= 3
    win_payout = np.where(win, win_odds, 0.0)
    plc_payout = np.where(plc, np.nan_to_num(plc_odds, nan=0.0), 0.0)
    win_roi = win_payout.sum() / n
    plc_roi = plc_payout.sum() / n
    win_boot = [rng.choice(win_payout, size=n, replace=True).mean() for _ in range(n_boot)]
    plc_boot = [rng.choice(plc_payout, size=n, replace=True).mean() for _ in range(n_boot)]
    wlo, whi = np.percentile(win_boot, [2.5, 97.5])
    plo, phi = np.percentile(plc_boot, [2.5, 97.5])
    return {"n": n, "win": win.mean() * 100, "plc": plc.mean() * 100,
            "win_roi": win_roi, "plc_roi": plc_roi,
            "win_lo": wlo, "win_hi": whi, "plc_lo": plo, "plc_hi": phi}


def segment_table(df: pd.DataFrame, col: str, title: str, rng: np.random.Generator,
                   order: list[str] | None = None, min_n: int = 15) -> None:
    print(f"\n--- {title} ---")
    print(f"  {'segment':<14}{'n':>6}{'単勝的中':>9}{'単ROI':>8}{'単CI':>15}"
          f"{'複勝的中':>9}{'複ROI':>8}{'複CI':>15}")
    keys = order if order is not None else sorted(df[col].dropna().unique())
    for k in keys:
        sub = df[df[col] == k]
        if len(sub) < min_n:
            continue
        st = _roi_row(sub, rng)
        wmark = "★" if st["win_lo"] > 1.0 else ("▼" if st["win_hi"] < 1.0 else " ")
        pmark = "★" if st["plc_lo"] > 1.0 else ("▼" if st["plc_hi"] < 1.0 else " ")
        print(f"  {str(k):<14}{st['n']:>6}{st['win']:>8.1f}%{st['win_roi']:>7.3f}{wmark}"
              f" [{st['win_lo']:.2f},{st['win_hi']:.2f}]"
              f"{st['plc']:>8.1f}%{st['plc_roi']:>7.3f}{pmark}"
              f" [{st['plc_lo']:.2f},{st['plc_hi']:.2f}]")


def _odds_band(o: float) -> str:
    if o < 15:
        return "10-15"
    if o < 20:
        return "15-20"
    if o < 30:
        return "20-30"
    if o < 50:
        return "30-50"
    return "50+"


def _dist_band(d: float) -> str:
    if d <= 1400:
        return "スプリント"
    if d <= 1800:
        return "マイル"
    if d <= 2400:
        return "中距離"
    return "長距離"


def _head_band(n: int) -> str:
    if n <= 8:
        return "-8"
    if n <= 12:
        return "9-12"
    if n <= 16:
        return "13-16"
    return "17+"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--end", default="20260725")
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

    cand = build_upset_candidates(df)
    logger.info("「穴」該当馬(K=1): %d件", len(cand))

    cand["odds_band"] = cand["win_odds"].apply(_odds_band)
    cand["dist_band"] = cand["distance"].apply(_dist_band)
    cand["head_band"] = cand["head_count"].apply(_head_band)
    cand["badge_str"] = cand["badge_cnt"].apply(lambda b: "1" if b == 1 else "2+")
    cand["comp_rank_band"] = cand["composite_rank"].apply(
        lambda r: "1-3位" if r <= 3 else ("4-6位" if r <= 6 else "7位+"))

    train = cand[cand["date"] < args.train_end]
    test = cand[cand["date"] >= args.test_start]

    print("\n" + "#" * 100)
    print("# JRA 「穴」タグ該当馬(本番K=1選定と同一) ROIセグメント分析")
    print("# ★=95%CI下限>1(黒字確証) / ▼=95%CI上限<1(赤字確証) / min_n=15未満は非表示")
    print(f"# train+val: {args.start}-{args.train_end} (n={len(train)}) / "
          f"test: {args.test_start}-{args.end} (n={len(test)})")
    print("#" * 100)

    for label, d in (("train+val", train), ("test(OOS)", test)):
        print(f"\n{'='*35} {label} {'='*35}")
        segment_table(d, "surface", "① 芝/ダート別", rng)
        segment_table(d, "dist_band", "② 距離帯別", rng,
                      ["スプリント", "マイル", "中距離", "長距離"])
        segment_table(d, "odds_band", "③ オッズ帯別", rng,
                      ["10-15", "15-20", "20-30", "30-50", "50+"])
        segment_table(d, "head_band", "④ 頭数帯別", rng, ["-8", "9-12", "13-16", "17+"])
        segment_table(d, "badge_str", "⑤ badge_cnt別(1 vs 2+)", rng, ["1", "2+"])
        segment_table(d, "comp_rank_band", "⑥ 指数順位帯別", rng, ["1-3位", "4-6位", "7位+"])
        segment_table(d, "course_name", "⑦ 競馬場別", rng)


if __name__ == "__main__":
    main()
