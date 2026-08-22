#!/usr/bin/env python3
"""形の比較を **出荷実装に忠実な軸選定** でやり直す（再現確認）。

## なぜ別スクリプトなのか

`exp_tf_shape_eval.py` は形だけを見たかったので軸選定を簡易化していた
（軸1 = 1着率最上位 / 軸2 = 3着内率最上位）。本番 `rank_7t1_select` は

  - 軸1 を **3着内率の上位 `RANK_7T1_AXIS1_TOP_N`(=2) 車**に限定し
  - **42順序対すべて**について点数 k を自己整合で決め
  - 目的値（Σ Plackett-Luce 確率）が最大の対を採る

という総当たりで、選ばれる軸が変わりうる。

🔴 **A/B 実装で採用ラインに達した案が、出荷実装で再現せず不採用になった前例がある**
（[[keirin_orphan_signals_ab_2026_08_21]]・Elo残差。変えたのはウォームアップ長だけで
効果量と同オーダー動いた）。**形を実装する前に、出荷する形で測り直すこと。**

ここでは `rank_7t1_select` と同じ探索を、候補脚の作り方だけ差し替えて再実装する
（本体を書き換えずに比較するため。採用が決まったら `strategy_wt` 側へ
`shape` 引数として入れる）。
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_tf_shape_eval import load, pops  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7T1_AXIS1_TOP_N, RANK_7T1_BUDGET, RANK_7T1_KMAX, RANK_7T1_UNIT,
    _rank_7t1_min_odds, rank_7t1_pl_prob, rank_7t1_stakes,
)

random.seed(5)


def _legs(shape: str, a1: int, a2: int, order: list[int]) -> list[str]:
    rest = [c for c in order if c not in (a1, a2)]
    if shape == "F1":
        return [f"{a1}-{a2}-{c}" for c in rest]
    # F3: 軸2 は2着でも3着でもよい（両順）
    out = []
    for c in rest:
        out.append(f"{a1}-{a2}-{c}")
        out.append(f"{a1}-{c}-{a2}")
    return out


def select_prod(shape: str, p3: dict[int, float], pw: dict[int, float],
                odds: dict[str, float], board: set[str], target: int,
                kmax: int) -> tuple[int, int, list[str]] | None:
    """`rank_7t1_select` と同じ総当たり。候補脚の作り方だけ shape で差し替える。"""
    order = [f for f, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
    if len(order) < 3:
        return None
    allow = set(order[:RANK_7T1_AXIS1_TOP_N])
    best = None
    for a1 in order:
        if a1 not in allow:
            continue
        for a2 in order:
            if a2 == a1:
                continue
            scored = []
            for leg in _legs(shape, a1, a2, order):
                if leg not in board:
                    continue          # 欠車で消えた目
                pl = rank_7t1_pl_prob(pw, leg)
                o = odds.get(leg)
                if pl is None or pl <= 0 or not o or o <= 0:
                    continue
                scored.append((pl, float(o), leg))
            if not scored:
                continue
            scored.sort(key=lambda t: -t[0])
            for k in range(1, min(kmax, len(scored)) + 1):
                bar = _rank_7t1_min_odds(k, target, RANK_7T1_BUDGET, RANK_7T1_UNIT)
                if bar is None:
                    break
                feas = [t for t in scored if t[1] >= bar]
                if len(feas) < k:
                    break
                obj = sum(t[0] for t in feas[:k])
                if best is None or obj > best[0]:
                    best = (obj, a1, a2, [t[2] for t in feas[:k]])
    return (best[1], best[2], best[3]) if best else None


def build(rows, shape, target, pop, kmax):
    by_day = defaultdict(list)
    for row in rows:
        p3 = {int(k): v for k, v in row["p3"].items()}
        pw = {int(k): v for k, v in row["pw"].items()}
        if not pops(row, p3)[pop]:
            continue
        got = select_prod(shape, p3, pw, row["odds"], row["_board"], target, kmax)
        if got is None:
            continue
        _, _, legs = got
        stakes = rank_7t1_stakes(legs)
        bet = sum(stakes.values())
        payout = next((row["win"][l] * stakes[l] // 100 for l in legs
                       if l in row["win"]), 0)
        by_day[row["race_date"]].append(dict(
            bet=bet, payout=payout, n=len(legs),
            ev=sum((rank_7t1_pl_prob(pw, l) or 0.0) * row["odds"].get(l, 0.0)
                   * stakes[l] for l in legs) / max(bet, 1)))
    return by_day


def days_of(by_day, cap, t_eval):
    out = []
    for d, ps in sorted(by_day.items()):
        sel = sorted(ps, key=lambda r: -r["ev"])[:cap]
        if not sel:
            continue
        out.append((d, sum(x["bet"] for x in sel), sum(x["payout"] for x in sel),
                    len(sel), sum(1 for x in sel if x["payout"] >= t_eval),
                    sum(1 for x in sel if x["payout"] > 0),
                    st.mean([x["n"] for x in sel])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--target", type=int, default=200_000)
    ap.add_argument("--t-eval", type=int, default=150_000)
    ap.add_argument("--cap", type=int, default=20)
    args = ap.parse_args()

    rows = load(args.cache)
    print(f"出荷実装忠実版（軸1=3着内率上位{RANK_7T1_AXIS1_TOP_N}・42順序対総当たり）")
    print(f"T_sel={args.target:,} / T_eval={args.t_eval:,} / N={args.cap}\n")
    print(f"{'形':4}{'母集団':16}{'kmax':>5}{'件/日':>6}{'点':>5}{'ROI':>8}"
          f"{'到達/日':>8}{'100%超':>8}{'0円日':>7}{'的中/日':>8}")
    for shape, kmax in (("F1", RANK_7T1_KMAX), ("F3", 3), ("F3", 2)):
        for pop in ("全7車", "別ライン", "決勝系×別ライン"):
            d = days_of(build(rows, shape, args.target, pop, kmax),
                        args.cap, args.t_eval)
            n = len(d); b = sum(x[1] for x in d); p = sum(x[2] for x in d)
            rois = [x[2] / x[1] for x in d]
            print(f"{shape:4}{pop:16}{kmax:>5}{st.mean([x[3] for x in d]):>6.1f}"
                  f"{st.mean([x[6] for x in d]):>5.1f}{p / b:>8.1%}"
                  f"{sum(x[4] for x in d) / n:>8.2f}"
                  f"{sum(1 for r in rois if r >= 1) / n:>8.1%}"
                  f"{sum(1 for r in rois if r == 0) / n:>7.1%}"
                  f"{sum(x[5] for x in d) / n:>8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
