#!/usr/bin/env python3
"""7C の落差カット（`rank_7c_cut_legs_by_gap`）を**カット後の点数別**に評価する。

## なぜ要るのか（2026-08-15・ユーザー指摘）

7C は的中体験の商品（総流しで的中約59%）だが、落差カットで**1点まで縮む**ことがある。
2026-08-14 奈良9R は `1=2-4` の1点で外れ（実 4-1-3・軸2車は3着内なので総流しなら的中）。

落差カットを採用したときの検証（`RANK_7C_TRIO_GAP_MIN` の定義部）は
**全レースの平均**でしか見ておらず、「1点まで縮んだレース」だけを取り出していない。
平均で ROI +0.6〜1.2pt でも、その内訳が「2〜3点はプラス・1点は大損」なら
1点だけ止めれば得になる。ここではカット後の点数 k ごとに分解する。

## 比較する2つの買い方（同一母集団・同一予算）

    総流し   : 相手を削らず `rank_7c_select_legs` の結果すべて（4〜5点）
    落差カット: `rank_7c_cut_legs_by_gap` の結果（1〜5点）＝**現行の本番**

母集団は本番と同じ 7C 三連複側:
  `rank_7c_daily_select` の条件（p3_sum_top2 >= RANK_7C_P3_SUM_MIN ∧
  相手 >= RANK_7C_LEGS_MIN ∧ lowpay でない）を満たし、かつ三連複側の追加ゲート
  `p3_sum_top2 >= RANK_7C_TRIO_P3_SUM_MIN` を通ったレース
  （三連単へ切り替わるレース＝`rank_7c_use_trifecta` は落差カットを掛けないので除く）。

## 賭け金

1レース `RACE_BUDGET` を点数で等分（`unit_stake`）。**点数が変われば単価も変わる**ので、
1点になったレースも投資額は同じ1万円。ここを均等単価にすると「点数を減らすと
投資が減ってROIが良く見える」という偽の改善が出る。

⚠️ オッズ（`trio_legs`）は最終オッズ。**精算にのみ使う**（選別・相手選びには使わない）。
   カットは p3 のみで決まるのでオッズ由来の選択バイアスは入らない。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_gap_cut_by_size.py
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_gap_cut_by_size.py --gap 0.15 0.20 0.25
"""
from __future__ import annotations

import argparse
import pickle
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    RACE_BUDGET,
    RANK_7C_LEGS_MIN,
    RANK_7C_P3_SUM_MIN,
    RANK_7C_TRIO_GAP_MIN,
    RANK_7C_TRIO_P3_SUM_MIN,
    rank_7c_cut_legs_by_gap,
    rank_7c_select_axis,
    rank_7c_select_legs,
    rank_7c_use_trifecta,
    unit_stake,
)

CACHE = REPO / "data" / "exp_7c_cache"
#: 掃引窓／確認窓の境界。`RANK_7C_TRIO_GAP_MIN` を決めたときと同じ切り方にする。
CONFIRM_END = "2025-06-30"


def _load() -> list[dict]:
    races: list[dict] = []
    for p in sorted(CACHE.glob("*.pkl")):
        with p.open("rb") as f:
            races.extend(pickle.load(f))
    return races


def _population(races: list[dict]) -> list[dict]:
    """本番の 7C 三連複側と同じ母集団へ絞り、総流し／カット後の相手を付ける。"""
    out = []
    for r in races:
        p3 = r.get("top3_probs") or {}
        if len(p3) != 7:
            continue
        axis = rank_7c_select_axis(p3)
        if not axis or len(axis) < 2:
            continue
        a1, a2 = axis[0], axis[1]
        p3_sum = sum(sorted(p3.values(), reverse=True)[:2])
        if p3_sum < RANK_7C_P3_SUM_MIN:
            continue
        others = [f for f in sorted(p3, key=lambda x: -p3[x]) if f not in (a1, a2)]
        legs = rank_7c_select_legs(others, p3)
        if len(legs) < RANK_7C_LEGS_MIN:
            continue
        # 三連単へ切り替わるレースはカットを掛けない＝本検証の対象外
        if rank_7c_use_trifecta(r.get("win_probs") or {}, a1):
            continue
        # 三連複側だけの追加ゲート
        if p3_sum < RANK_7C_TRIO_P3_SUM_MIN:
            continue
        out.append({**r, "axis1": a1, "axis2": a2, "legs": legs, "p3": p3})
    return out


def _settle(race: dict, legs: list[int]) -> tuple[int, int, bool]:
    """(投資, 払戻, 的中)。予算は点数によらず RACE_BUDGET を等分する。"""
    if not legs:
        return 0, 0, False
    unit = unit_stake(len(legs))
    bet = unit * len(legs)
    top3 = frozenset(race["actual_top3"])
    bought = {frozenset({race["axis1"], race["axis2"], x}) for x in legs}
    if top3 not in bought:
        return bet, 0, False
    # trio_pay は「100円あたりの確定配当」
    pay = int(race.get("trio_pay") or 0) * unit // 100
    return bet, pay, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, nargs="*", default=[RANK_7C_TRIO_GAP_MIN])
    args = ap.parse_args()

    pop = _population(_load())
    if not pop:
        print("母集団が空です（キャッシュを確認してください）", file=sys.stderr)
        return 1
    print(f"母集団（7C 三連複側・落差カットの対象）: {len(pop)} R")
    print(f"  掃引窓 〜{CONFIRM_END} / 確認窓 {CONFIRM_END} 以降\n")

    for gap in args.gap:
        print(f"══ gap_min = {gap} ══")
        # ① カット後の点数 k ごとに「カット vs 総流し」を並べる
        by_k: dict[int, dict] = defaultdict(
            lambda: {"n": 0, "cut_bet": 0, "cut_pay": 0, "cut_hit": 0,
                     "full_bet": 0, "full_pay": 0, "full_hit": 0})
        for r in pop:
            cut = rank_7c_cut_legs_by_gap(r["legs"], r["p3"], gap_min=gap)
            k = len(cut)
            b = by_k[k]
            b["n"] += 1
            for tag, legs in (("cut", cut), ("full", r["legs"])):
                bet, pay, hit = _settle(r, legs)
                b[f"{tag}_bet"] += bet
                b[f"{tag}_pay"] += pay
                b[f"{tag}_hit"] += int(hit)

        print(f"{'カット後':<8}{'R数':>6}{'割合':>7} │"
              f"{'的中(カット)':>12}{'ROI':>8} │{'的中(総流し)':>13}{'ROI':>8} │{'ROI差':>8}")
        for k in sorted(by_k):
            b = by_k[k]
            n = b["n"]
            cr = b["cut_pay"] / b["cut_bet"] if b["cut_bet"] else 0
            fr = b["full_pay"] / b["full_bet"] if b["full_bet"] else 0
            print(f"{k}点{'':<6}{n:>6}{n/len(pop):>7.1%} │"
                  f"{b['cut_hit']/n:>12.1%}{cr:>8.1%} │{b['full_hit']/n:>13.1%}{fr:>8.1%} │"
                  f"{cr - fr:>+8.1f}pt" if False else
                  f"{k}点{'':<6}{n:>6}{n/len(pop):>7.1%} │"
                  f"{b['cut_hit']/n:>12.1%}{cr:>8.1%} │{b['full_hit']/n:>13.1%}{fr:>8.1%} │"
                  f"{(cr - fr) * 100:>+7.1f}pt")

        # ② 「k点以下なら総流しに戻す」下限を掃引したときの全体成績
        print(f"\n{'方針':<28}{'R数':>6}{'的中率':>9}{'ROI':>9}{'ROI(掃引)':>11}{'ROI(確認)':>11}")
        for floor in (0, 1, 2, 3, 99):
            tot = {"n": 0, "bet": 0, "pay": 0, "hit": 0}
            win = {"sweep": {"bet": 0, "pay": 0}, "confirm": {"bet": 0, "pay": 0}}
            for r in pop:
                cut = rank_7c_cut_legs_by_gap(r["legs"], r["p3"], gap_min=gap)
                legs = r["legs"] if len(cut) <= floor else cut
                bet, pay, hit = _settle(r, legs)
                tot["n"] += 1
                tot["bet"] += bet
                tot["pay"] += pay
                tot["hit"] += int(hit)
                w = "sweep" if str(r.get("race_date", "")) <= CONFIRM_END else "confirm"
                win[w]["bet"] += bet
                win[w]["pay"] += pay
            label = {0: "現行（常にカット）", 99: "カットしない（総流し）"}.get(
                floor, f"{floor}点以下なら総流しへ戻す")
            sr = win["sweep"]["pay"] / win["sweep"]["bet"] if win["sweep"]["bet"] else 0
            cr = win["confirm"]["pay"] / win["confirm"]["bet"] if win["confirm"]["bet"] else 0
            print(f"{label:<28}{tot['n']:>6}{tot['hit']/tot['n']:>9.1%}"
                  f"{tot['pay']/tot['bet']:>9.1%}{sr:>11.1%}{cr:>11.1%}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
