"""JRA 穴ぐさ(sekito.anagusa)ピックのROIセグメント分析

ユーザー要望(2026-07-25): 穴ぐさピックの好走条件・ROIを確保できる買い方が
ないか検証する。[[jra_axis_segment_deny_analysis]]/[[jra_upset_candidate_roi_segment]]
と同じ方法論(train+val発見→testで一度きり確認)で、anagusa_rank(A/B/C)単独に
加え、指数(composite)順位・オッズ帯・馬場・距離帯・頭数帯等のセグメントで
単勝/複勝ROIを検証する。

使い方:
  cd backend
  .venv/bin/python scripts/jra_anagusa_roi_segment.py
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
logger = logging.getLogger("jra_anagusa_roi")


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
    if o < 3:
        return "<3"
    if o < 6:
        return "3-6"
    if o < 10:
        return "6-10"
    if o < 20:
        return "10-20"
    if o < 50:
        return "20-50"
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


def _comp_rank_band(r: int) -> str:
    if r <= 2:
        return "1-2位"
    if r <= 5:
        return "3-5位"
    if r <= 9:
        return "6-9位"
    return "10位+"


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

    ag = df[df["anagusa_rank"].isin(["A", "B", "C"])].copy()
    logger.info("穴ぐさピック該当馬: %d件", len(ag))

    ag["odds_band"] = ag["win_odds"].apply(_odds_band)
    ag["dist_band"] = ag["distance"].apply(_dist_band)
    ag["head_band"] = ag["head_count"].apply(_head_band)
    ag["comp_rank_band"] = ag["composite_rank"].apply(_comp_rank_band)
    ag["model_agrees"] = np.where(ag["composite_rank"] <= 3, "指数上位3以内", "指数下位")

    train = ag[ag["date"] < args.train_end]
    test = ag[ag["date"] >= args.test_start]

    print("\n" + "#" * 100)
    print("# JRA 穴ぐさピック(anagusa A/B/C) ROIセグメント分析")
    print("# ★=95%CI下限>1(黒字確証) / ▼=95%CI上限<1(赤字確証) / min_n=15未満は非表示")
    print(f"# train+val: {args.start}-{args.train_end} (n={len(train)}) / "
          f"test: {args.test_start}-{args.end} (n={len(test)})")
    print("#" * 100)

    for label, d in (("train+val", train), ("test(OOS)", test)):
        print(f"\n{'='*35} {label} {'='*35}")
        segment_table(d, "anagusa_rank", "① ランク別(A/B/C)", rng, ["A", "B", "C"])
        segment_table(d, "comp_rank_band", "② 指数順位帯別", rng, ["1-2位", "3-5位", "6-9位", "10位+"])
        segment_table(d, "model_agrees", "③ 指数上位3以内か否か", rng, ["指数上位3以内", "指数下位"])
        segment_table(d, "odds_band", "④ オッズ帯別", rng, ["<3", "3-6", "6-10", "10-20", "20-50", "50+"])
        segment_table(d, "surface", "⑤ 芝/ダート別", rng)
        segment_table(d, "dist_band", "⑥ 距離帯別", rng, ["スプリント", "マイル", "中距離", "長距離"])
        segment_table(d, "head_band", "⑦ 頭数帯別", rng, ["-8", "9-12", "13-16", "17+"])
        segment_table(d, "course_name", "⑧ 競馬場別", rng)

        print(f"\n--- ⑨ anagusa_rank × 指数上位3以内 クロス集計 ---")
        print(f"  {'rank':<4}{'model':<10}{'n':>6}{'単勝的中':>9}{'単ROI':>8}"
              f"{'複勝的中':>9}{'複ROI':>8}")
        for r in ["A", "B", "C"]:
            for m in ["指数上位3以内", "指数下位"]:
                sub = d[(d["anagusa_rank"] == r) & (d["model_agrees"] == m)]
                if len(sub) < 15:
                    continue
                st = _roi_row(sub, rng, n_boot=500)
                print(f"  {r:<4}{m:<10}{st['n']:>6}{st['win']:>8.1f}%{st['win_roi']:>7.3f}"
                      f"{st['plc']:>8.1f}%{st['plc_roi']:>7.3f}")


if __name__ == "__main__":
    main()
