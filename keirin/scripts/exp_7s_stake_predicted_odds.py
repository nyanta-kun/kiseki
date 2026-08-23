#!/usr/bin/env python3
"""配分を**予測オッズ**で組んだときのガミ率（2026-08-23・実力値）。

## なぜ

`exp_7s_stake_contrast` / `exp_7s_leg_gap_line_cut` は**確定オッズ**で配分していた。
本番は朝の**予測オッズ**で配分するので、あれは上限の数字。
「1.2倍のつもりが下回る」ぶんガミ率は実際にはもっと高い。**その実力値を出す。**

    配分 = 予測オッズ（`odds_prediction.predicted_trio_board`・朝の情報のみ）
    採点 = 確定オッズ（`wt_odds`）

⚠️ 予測オッズが作れないレースがある（実測 約3.7%）。**その場合は現行の均等配分へ
   落とす**（本番も同じ扱いにすること。落とし先が無いと商品が消える）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_7s_leg_gap_line_cut import cut  # noqa: E402
from scripts.exp_7s_leg_selection import CONFIRM, SEARCH_END, build  # noqa: E402
from scripts.exp_7s_stake_contrast import allocate  # noqa: E402
from scripts.exp_trio_joint_partner import load_boards  # noqa: E402
from src.odds_prediction import OddsPredictionUnavailable, predicted_trio_board  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402

ARMS = [
    ("現行(5点総流し・均等)", None, False, "現行(均等)"),
    ("断層0.25＋ライン保護＋均等", 0.25, True, "現行(均等)"),
    ("断層0.25＋ライン保護＋B配分", 0.25, True, "B:下位2点を1.2倍"),
    ("断層0.25＋ライン保護＋C配分", 0.25, True, "C:下位3点を1.2倍"),
    ("断層0.25＋ライン保護＋D配分", 0.25, True, "D:全点ダッチング"),
]


def run(rows, board, fin, label, use_pred):
    src = "予測オッズ（実力値）" if use_pred else "確定オッズ（上限）"
    print(f"\n===== {label} ・ 配分に {src} ・ {len(rows):,}R =====")
    print(f"{'構成':>28}{'予測欠':>7}{'平均点':>7}{'的中%':>8}{'ROI':>8}"
          f"{'ガミ率':>8}{'中央払戻':>10}{'100%超の日':>11}")
    pred_cache: dict[str, dict | None] = {}
    for name, gap, guard, alloc in ARMS:
        d = defaultdict(lambda: [0.0, 0.0])
        n = hit = gami = nofall = 0
        legs_n, pays = [], []
        for r in rows:
            b = board.get(r["key"]); o3 = fin.get(r["key"])
            if not b or not o3:
                continue
            keep, _ = (r["rest"], []) if gap is None else cut(r, gap, guard)
            ks = {c: frozenset((r["a1"], r["a2"], c)) for c in keep}
            ks = {c: k for c, k in ks.items() if k in b}
            if not ks:
                continue
            final = {c: b[k] for c, k in ks.items()}
            if use_pred and alloc != "現行(均等)":
                if r["key"] not in pred_cache:
                    try:
                        pred_cache[r["key"]] = predicted_trio_board(r["key"])
                    except (OddsPredictionUnavailable, Exception):
                        pred_cache[r["key"]] = None
                pb = pred_cache[r["key"]]
                if pb is None or any(ks[c] not in pb for c in ks):
                    nofall += 1
                    alloc_odds, alloc_use = final, "現行(均等)"   # 🔴 均等へ落とす
                else:
                    alloc_odds, alloc_use = {c: pb[ks[c]] for c in ks}, alloc
            else:
                alloc_odds, alloc_use = final, alloc
            st = allocate(alloc_use, [c for c in r["rest"] if c in ks], alloc_odds)
            if st is None:
                st = {c: max(100, (10000 // len(ks)) // 100 * 100) for c in ks}
                nofall += 1
            n += 1
            legs_n.append(len(ks))
            bet = sum(st.values())
            pay = sum(int(final[c] * 100) * st[c] // 100
                      for c, k in ks.items() if k in r["wins"])
            h = any(k in r["wins"] for k in ks.values())
            hit += h
            if h:
                pays.append(pay); gami += int(pay < bet)
            z = d[r["date"]]; z[0] += bet; z[1] += pay
        if n < 100:
            continue
        v = np.array([[x[0], x[1]] for x in d.values()], float)
        print(f"{name:>28}{nofall/max(n,1):>7.1%}{np.mean(legs_n):>7.2f}"
              f"{hit/n:>8.2%}{v[:, 1].sum()/v[:, 0].sum():>8.1%}"
              f"{gami/max(hit,1):>8.1%}{np.median(pays):>10,.0f}"
              f"{float(np.mean(v[:, 1] >= v[:, 0])):>11.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="both")
    args = ap.parse_args()
    wins = [("探索 2024-01〜2025-12", ("2024-01-01", SEARCH_END)),
            ("確認 2026-01〜06", CONFIRM)]
    if args.windows == "confirm":
        wins = wins[1:]
    for label, (lo, hi) in wins:
        rows = build(lo, hi)
        board = load_boards([r["key"] for r in rows])
        fin = _load_finishes([r["key"] for r in rows])
        run(rows, board, fin, label, use_pred=False)
        run(rows, board, fin, label, use_pred=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
