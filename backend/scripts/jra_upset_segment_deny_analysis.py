"""JRA「穴ロジック」再設計: 人気薄馬(単勝10倍+)の複勝的中セグメント異質性分析

現状は dm_signals.py(穴ぐさDM/DM大穴/DM高オッズ/穴ぐさ+DMtime の4タグ、各々に
場・距離帯deny filter)・is_external_dark_horse・anagusa_rank・upset_reranker.py
(ns logistic モデル)が独立に並立している。[[keirin_s1_axis_class_deny_filter_2026_07_22]]
と同じ方法論(train+val発見→testで一度きり評価)で、複数の独立情報源の一致数
(badge_cnt)や指数順位帯が、人気薄馬の複勝的中に対してどれだけ頑健な分離を
持つかを検証し、乱立したタグを一元化できる軸を探す。

使い方:
  cd backend
  .venv/bin/python scripts/jra_upset_segment_deny_analysis.py
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
logger = logging.getLogger("jra_upset_segment")

UPSET_MIN_ODDS = 10.0


def _roi_row(sub: pd.DataFrame, rng: np.random.Generator, n_boot: int = 2000) -> dict:
    """複勝的中率・複勝ROI(近似: place_oddsがあれば使用、無ければ的中率のみ)を返す。"""
    n = len(sub)
    if n == 0:
        return {"n": 0, "win": 0.0, "plc": 0.0, "plc_roi": 0.0, "lo": 0.0, "hi": 0.0}
    fp = sub["finish_position"].to_numpy()
    plc_odds = sub["place_odds"].to_numpy()
    win = fp == 1
    plc = fp <= 3
    payout = np.where(plc, np.nan_to_num(plc_odds, nan=0.0), 0.0)
    roi = payout.sum() / n
    boot = [rng.choice(payout, size=n, replace=True).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n": n, "win": win.mean() * 100, "plc": plc.mean() * 100,
            "plc_roi": roi, "lo": lo, "hi": hi}


def segment_table(df: pd.DataFrame, col: str, title: str, rng: np.random.Generator,
                   order: list[str] | None = None) -> None:
    print(f"\n--- {title} ---")
    print(f"  {'segment':<16}{'n':>7}{'単勝的中':>10}{'複勝的中':>10}{'複勝ROI':>9}{'CI':>16}")
    keys = order if order is not None else sorted(df[col].dropna().unique())
    for k in keys:
        sub = df[df[col] == k]
        if len(sub) == 0:
            continue
        st = _roi_row(sub, rng)
        mark = "★" if st["lo"] > 1.0 else ("▼" if st["hi"] < 1.0 else " ")
        print(f"  {str(k):<16}{st['n']:>7}{st['win']:>9.1f}%{st['plc']:>9.1f}%"
              f"{st['plc_roi']:>8.3f}{mark}  [{st['lo']:.2f},{st['hi']:.2f}]")


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

    # レース内 DM battle 順位（1=最良）
    def _dm_battle_rank(g: pd.DataFrame) -> pd.Series:
        d = g["jvan_battle_dm"]
        if d.notna().sum() < 2:
            return pd.Series([np.nan] * len(g), index=g.index)
        return d.rank(method="min", ascending=False)
    df["dm_battle_rank"] = df.groupby("race_id", group_keys=False).apply(_dm_battle_rank)

    # 人気薄母集団（単勝オッズ >= 10）に絞る
    unpop = df[df["win_odds"] >= UPSET_MIN_ODDS].copy()

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
    unpop["badge_cnt"] = unpop.apply(_badge_cnt, axis=1).astype(str)

    def _comp_bucket(r: int) -> str:
        if r <= 3:
            return "1-3位"
        if r <= 6:
            return "4-6位"
        return "7位+"
    unpop["comp_bucket"] = unpop["composite_rank"].apply(_comp_bucket)

    def _odds_band(o: float) -> str:
        if o < 15:
            return "10-15"
        if o < 20:
            return "15-20"
        if o < 30:
            return "20-30"
        return "30+"
    unpop["odds_band"] = unpop["win_odds"].apply(_odds_band)

    unpop["anagusa_flag"] = np.where(unpop["anagusa_rank"].isin(["A", "B", "C"]), "穴ぐさ有", "穴ぐさ無")
    unpop["dm_flag"] = np.where(unpop["dm_battle_rank"] <= 2, "DM上位", "DM非上位")
    unpop["nb_flag"] = np.where(unpop["nb_ave_rank"] <= 3, "NB上位", "NB非上位")
    unpop["km_flag"] = np.where(unpop["km_rank"] <= 3, "KM上位", "KM非上位")

    train = unpop[unpop["date"] < args.train_end]
    test = unpop[unpop["date"] >= args.test_start]

    print("\n" + "#" * 92)
    print("# JRA 穴ロジック セグメント異質性分析（単勝オッズ>=10 の人気薄馬・複勝的中/ROI）")
    print("# ★=95%CI下限>1(黒字確証) / ▼=95%CI上限<1(赤字確証)")
    print(f"# train+val: {args.start}-{args.train_end} (n={len(train)}) / "
          f"test: {args.test_start}-{args.end} (n={len(test)})")
    print("#" * 92)

    for label, d in (("train+val", train), ("test(OOS)", test)):
        print(f"\n{'='*30} {label} {'='*30}")
        segment_table(d, "badge_cnt", "① badge_cnt(独立情報源一致数 0-4)別", rng,
                      ["0", "1", "2", "3", "4"])
        segment_table(d, "comp_bucket", "② 指数(composite)順位帯別", rng, ["1-3位", "4-6位", "7位+"])
        segment_table(d, "odds_band", "③ オッズ帯別", rng, ["10-15", "15-20", "20-30", "30+"])
        segment_table(d, "anagusa_flag", "④ 穴ぐさ有無単独", rng, ["穴ぐさ有", "穴ぐさ無"])
        segment_table(d, "dm_flag", "⑤ DM battle上位単独", rng, ["DM上位", "DM非上位"])
        segment_table(d, "nb_flag", "⑥ netkeiba上位単独", rng, ["NB上位", "NB非上位"])
        segment_table(d, "km_flag", "⑦ kichiuma上位単独", rng, ["KM上位", "KM非上位"])

        print(f"\n--- ⑧ badge_cnt × comp_bucket クロス集計 ---")
        print(f"  {'badge':<6}{'comp':<8}{'n':>7}{'単勝的中':>10}{'複勝的中':>10}{'複勝ROI':>9}")
        for bc in ["0", "1", "2", "3", "4"]:
            for cb in ["1-3位", "4-6位", "7位+"]:
                sub = d[(d["badge_cnt"] == bc) & (d["comp_bucket"] == cb)]
                if len(sub) < 5:
                    continue
                st = _roi_row(sub, rng, n_boot=500)
                print(f"  {bc:<6}{cb:<8}{st['n']:>7}{st['win']:>9.1f}%"
                      f"{st['plc']:>9.1f}%{st['plc_roi']:>8.3f}")


if __name__ == "__main__":
    main()
