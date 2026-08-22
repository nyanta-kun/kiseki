#!/usr/bin/env python3
"""形の比較で出た候補を、掃引の外側の指標で検査する。

🔴 **掃引の最良セルをそのまま採用してはいけない。** 27セル振って最良を採れば
上振れを掴む。ここで見るのは:

  1. **日ブロック・ブートストラップ CI**（レース単位で resample すると日内相関を
     無視して CI が狭く出る）
  2. **月次一貫性**（8ヶ月で符号が揃うか）。ROI ではなく **到達/日** と
     **100%超の日** で見る。7T1 帯の ROI は CI が ±20pt 級で月次判定に使えない
  3. **日次選別の順序依存**。ev 上位で採ると「最も当たらない群」を掴む前例がある
     （[[keirin_min_point_odds_and_title_2026_08_22]]）ので、順序を変えて
     結果がひっくり返らないかを見る

⚠️ ここを通っても**確認窓にはならない**。同じ 2026-01〜08 を見ているので、
   前向きの実績で確かめること。
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_tf_shape_eval import (  # noqa: E402
    SHAPES, _KMAX, axis_simple, load, pops, select,
)
from src.strategy_wt import rank_7t1_pl_prob  # noqa: E402

random.seed(0)


def races(rows, shape, t_sel, pop):
    """(日, レース) 単位の購入結果。日次選別の前まで。"""
    out = defaultdict(list)
    for row in rows:
        p3 = {int(k): v for k, v in row["p3"].items()}
        pw = {int(k): v for k, v in row["pw"].items()}
        if not pops(row, p3)[pop]:
            continue
        a1, a2 = axis_simple(p3, pw)
        cand = [l for l in SHAPES[shape](a1, a2, sorted(p3)) if l in row["_board"]]
        if not cand:
            continue
        sel = select(cand, row["odds"], pw, t_sel, _KMAX[shape])
        if sel is None:
            continue
        legs, stake = sel
        payout = next((row["win"][l] * stake // 100 for l in legs if l in row["win"]), 0)
        p_hit = sum(rank_7t1_pl_prob(pw, l) or 0.0 for l in legs)
        out[row["race_date"]].append(dict(
            bet=stake * len(legs), payout=payout, n=len(legs), p_hit=p_hit,
            ev=sum((rank_7t1_pl_prob(pw, l) or 0.0) * row["odds"].get(l, 0.0)
                   for l in legs) * stake / max(stake * len(legs), 1),
            min_odds=min(row["odds"].get(l, 0.0) for l in legs),
            pop_rank=-p3[a1]))
    return out


ORDERS = {
    "ev降順": lambda r: -r["ev"],
    "的中確率降順": lambda r: -r["p_hit"],
    "予測オッズ昇順": lambda r: r["min_odds"],
    "軸1の3着内率降順": lambda r: r["pop_rank"],
    "無作為": lambda r: random.random(),
}


def days_of(by_day, order, cap, t_eval):
    rows = []
    for d, ps in sorted(by_day.items()):
        sel = sorted(ps, key=order)[:cap]
        if not sel:
            continue
        b = sum(x["bet"] for x in sel); p = sum(x["payout"] for x in sel)
        rows.append((d, b, p, len(sel), sum(1 for x in sel if x["payout"] >= t_eval),
                     sum(1 for x in sel if x["payout"] > 0)))
    return rows


def boot(days, B=2000):
    o = []
    for _ in range(B):
        s = [days[random.randrange(len(days))] for _ in days]
        b = sum(x[1] for x in s); p = sum(x[2] for x in s)
        o.append(p / b if b else 0)
    o.sort()
    return o[int(B * .025)], o[int(B * .975)]


def summarize(days, label):
    n = len(days)
    b = sum(x[1] for x in days); p = sum(x[2] for x in days)
    rois = [x[2] / x[1] for x in days]
    lo, hi = boot(days)
    print(f"{label:22}{n:>5}{st.mean([x[3] for x in days]):>6.1f}{p / b:>8.1%}"
          f"  [{lo:>5.1%},{hi:>6.1%}]{sum(x[4] for x in days) / n:>8.2f}"
          f"{sum(1 for r in rois if r >= 1) / n:>8.1%}{sum(1 for r in rois if r == 0) / n:>7.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--shape", default="F3")
    ap.add_argument("--pop", default="全7車")
    ap.add_argument("--t-sel", type=int, default=200_000)
    ap.add_argument("--t-eval", type=int, default=150_000)
    ap.add_argument("--cap", type=int, default=15)
    args = ap.parse_args()

    rows = load(args.cache)
    by_day = races(rows, args.shape, args.t_sel, args.pop)
    print(f"{args.shape} / {args.pop} / T_sel={args.t_sel:,} / "
          f"T_eval={args.t_eval:,} / N={args.cap}\n")

    print(f"{'日次選別の順序':22}{'日':>5}{'件/日':>6}{'ROI':>8}{'CI95':>17}"
          f"{'到達/日':>8}{'100%超':>8}{'0円日':>7}")
    for name, order in ORDERS.items():
        summarize(days_of(by_day, order, args.cap, args.t_eval), name)

    print(f"\n--- 月次（順序=ev降順）---")
    d = days_of(by_day, ORDERS["ev降順"], args.cap, args.t_eval)
    bym = defaultdict(list)
    for x in d:
        bym[x[0][:7]].append(x)
    print(f"{'月':10}{'日':>4}{'ROI':>8}{'到達/日':>8}{'100%超':>8}{'的中/日':>8}")
    for m, xs in sorted(bym.items()):
        b = sum(x[1] for x in xs); p = sum(x[2] for x in xs)
        rois = [x[2] / x[1] for x in xs]
        print(f"{m:10}{len(xs):>4}{p / b:>8.1%}{sum(x[4] for x in xs) / len(xs):>8.2f}"
              f"{sum(1 for r in rois if r >= 1) / len(xs):>8.1%}"
              f"{sum(x[5] for x in xs) / len(xs):>8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
