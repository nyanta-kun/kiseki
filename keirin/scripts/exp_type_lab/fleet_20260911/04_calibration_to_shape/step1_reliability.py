#!/usr/bin/env python3
"""p3 の較正崩れを (a) p3水準別 (b) レース内集中度別 (c) 予測オッズ帯別 に実測する。

台: /tmp/race_type_board.npz（共通ライブラリ scripts/exp_type_lab/common.py 経由）。
P3 は生の vintage 予測（type_lab.py の axis_sum が使っているのと同じ値・較正未適用）。
"""
from __future__ import annotations

import itertools
import sys

import numpy as np

sys.path.insert(0, "scripts/exp_type_lab")
import common  # noqa: E402

CANON = common.CANON  # permutations(range(1,8), 3)


def horse_level_frame(window: str):
    """race,horse ごとに (p3, is_top3, axis_sum(race), pw, rank_in_race) を展開する。"""
    z = common.board()
    idx = common.select(None, window)
    P3 = z["P3"][idx]            # (n,7)
    PW = z["PW"][idx]
    AX = z["AXIS_SUM"][idx]
    WIN = z["WIN"][idx]
    n = len(idx)
    top3_mask = np.zeros((n, 7), dtype=bool)
    for i in range(n):
        combo = CANON[int(WIN[i])]
        for c in combo:
            top3_mask[i, c - 1] = True
    finite = np.isfinite(P3).all(axis=1)
    P3, PW, AX, top3_mask = P3[finite], PW[finite], AX[finite], top3_mask[finite]
    n = len(P3)
    # レース内 p3 降順の順位 (0=軸1, 1=軸2, ...)
    order = np.argsort(-P3, axis=1)
    rank = np.empty_like(order)
    for i in range(n):
        rank[i, order[i]] = np.arange(7)
    return dict(p3=P3, top3=top3_mask.astype(np.float64), axis_sum=AX, pw=PW, rank=rank, n=n)


def reliability_by_p3_level(frames, bin_edges):
    print("\n## (a) p3 水準別（両窓・全馬・explore窓の分位で切って揃える）")
    print("  {:>14s} {:>8s} {:>10s} {:>10s} {:>8s}".format(
        "p3帯", "窓", "n", "予測p3平均", "実測top3%"))
    for window in ("explore", "confirm"):
        f = frames[window]
        p3, top3 = f["p3"].ravel(), f["top3"].ravel()
        for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
            m = (p3 >= lo) & (p3 < hi) if hi < bin_edges[-1] else (p3 >= lo) & (p3 <= hi)
            if m.sum() == 0:
                continue
            print("  [{:.3f},{:.3f}) {:>8s} {:>10d} {:>10.4f} {:>8.4f}".format(
                lo, hi, window, int(m.sum()), p3[m].mean(), top3[m].mean()))


def reliability_by_concentration(frames):
    print("\n## (b) レース内集中度別（axis_sum の五分位・explore窓のedgeを両窓へ適用）")
    f0 = frames["explore"]
    edges = np.quantile(f0["axis_sum"], [0, .2, .4, .6, .8, 1.0])
    edges[0], edges[-1] = -np.inf, np.inf
    labels = ["Q1(混戦)", "Q2", "Q3", "Q4", "Q5(堅い)"]
    print("  explore edge(axis_sum quantile):", np.round(edges[1:-1], 4))
    for window in ("explore", "confirm"):
        f = frames[window]
        print(f"\n  --- {window} 窓 ---")
        print("  {:>10s} {:>8s} {:>10s} {:>10s} {:>10s} {:>8s} | {:>10s} {:>10s} {:>8s}".format(
            "層", "n_race", "全馬pred", "全馬実測", "diff", "", "軸2車pred", "軸2車実測", "diff"))
        for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
            rm = (f["axis_sum"] >= lo) & (f["axis_sum"] < hi)
            n_race = int(rm.sum())
            if n_race == 0:
                continue
            p3_all = f["p3"][rm].ravel()
            top3_all = f["top3"][rm].ravel()
            axis_p3 = f["p3"][rm][f["rank"][rm] < 2]
            axis_top3 = f["top3"][rm][f["rank"][rm] < 2]
            print("  {:>10s} {:>8d} {:>10.4f} {:>10.4f} {:>+10.4f} {:>8s} | {:>10.4f} {:>10.4f} {:>+8.4f}".format(
                lab, n_race, p3_all.mean(), top3_all.mean(), top3_all.mean() - p3_all.mean(), "",
                axis_p3.mean(), axis_top3.mean(), axis_top3.mean() - axis_p3.mean()))


def reliability_by_odds_band(frames):
    print("\n## (c) 予測オッズ帯別（odds_est = 0.75/PW・全馬 と 軸2車のみ）")
    bands = [(0, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, 300), (300, np.inf)]
    for window in ("explore", "confirm"):
        f = frames[window]
        pw = f["pw"].ravel()
        p3 = f["p3"].ravel()
        top3 = f["top3"].ravel()
        rank = f["rank"].ravel()
        m0 = pw > 1e-6
        odds = np.where(m0, 0.75 / np.where(m0, pw, np.nan), np.nan)
        print(f"\n  --- {window} 窓 ---")
        print("  {:>10s} {:>10s} {:>10s} {:>10s} {:>8s} {:>8s} | {:>10s} {:>10s} {:>8s}".format(
            "帯(倍)", "n(全馬)", "pred", "実測", "実測/予測", "", "n(軸2車)", "pred", "実測"))
        for lo, hi in bands:
            m = (odds >= lo) & (odds < hi)
            if m.sum() == 0:
                continue
            ma = m & (rank < 2)
            ratio = top3[m].mean() / p3[m].mean() if p3[m].mean() > 0 else np.nan
            print("  {:>4.0f}-{:<5.0f} {:>10d} {:>10.4f} {:>10.4f} {:>8.3f} {:>8s} | {:>10d} {:>10.4f} {:>10.4f}".format(
                lo, hi, int(m.sum()), p3[m].mean(), top3[m].mean(), ratio, "",
                int(ma.sum()), p3[ma].mean() if ma.sum() else np.nan,
                top3[ma].mean() if ma.sum() else np.nan))


def main():
    frames = {w: horse_level_frame(w) for w in ("explore", "confirm")}
    for w in frames:
        print(w, "n_race=", frames[w]["n"])
    bin_edges = list(np.quantile(frames["explore"]["p3"].ravel(), np.linspace(0, 1, 11)))
    bin_edges[0], bin_edges[-1] = 0.0, 1.0
    reliability_by_p3_level(frames, bin_edges)
    reliability_by_concentration(frames)
    reliability_by_odds_band(frames)


if __name__ == "__main__":
    main()
