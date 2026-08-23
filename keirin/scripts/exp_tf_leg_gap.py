#!/usr/bin/env python3
"""三連単210通りの中に「市場が値付けを外している目」の勾配があるか。

## 問い（ユーザー 2026-08-22）

> 三連単も点数が多い分、ギャップが発生し、適切に買い目を絞れると期待値が取れる

三連複は35通りだが三連単は **210通り**。粒度が細かいぶん市場が個々の目を
正しく値付けしきれず、モデル側に妙味が残る余地がある、という仮説。

## なぜ今なら測れるのか

[[keirin_market_agree_transfer_2026_08_22]] で、現行の買い目確率
（`rank_7t1_pl_prob` ＝ **1着率のみ**の Plackett-Luce）が確率として明確に劣ることが
分かり、位置別合成 `s_i = pw^a_i · p3^(1−a_i)`（a=(1,.5,0)）で
top1 5.90→10.50%（確認窓でも再現）へ改善した。
**まともな確率が無ければ EV は測れない。** 従来 EV 系の検証が振るわなかった一因が
ここにある可能性がある。

## 測るもの

- **① 妙味の勾配**: 目を `EV = p_model × 予測オッズ` で分位に切り、
  **確定オッズで採点した実現 ROI** が単調に上がるか。
  🔴 上がらなければ「ギャップは市場に織り込まれている」＝ [[keirin_highpay_payout_ceiling_2026_08_06]]
     の帯ROI がフラットだったのと同じ結論になる
- **② 商品への伝播**: 7T2 の目の選び方を EV 順へ替えて KPI が動くか

🔴 **選別は予測オッズ・採点は確定オッズ**（朝8:00 に確定オッズは無い）。
🔴 **ROI で採否を決めない**が、①は「妙味があるか」そのものを問うているので
   ここでは実現 ROI が一次指標になる。ただし**日ブロックの CI を必ず付ける**。
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_leg_prob_heads import strengths  # noqa: E402

random.seed(41)


def leg_probs(pw, p3, a=(1.0, 0.5, 0.0)) -> dict[str, float]:
    s = [strengths(pw, p3, x) for x in a]
    cars = list(pw)
    tot = 0.0
    out = {}
    s1sum = sum(s[0].values())
    for x, y, z in itertools.permutations(cars, 3):
        d2 = sum(s[1][c] for c in cars if c != x)
        d3 = sum(s[2][c] for c in cars if c not in (x, y))
        if d2 <= 0 or d3 <= 0:
            continue
        v = (s[0][x] / s1sum) * (s[1][y] / d2) * (s[2][z] / d3)
        out[f"{x}-{y}-{z}"] = v
        tot += v
    return {k: v / tot for k, v in out.items()} if tot > 0 else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--bins", type=int, default=8)
    ap.add_argument("--min-odds", type=float, default=0.0,
                    help="この予測オッズ未満の目を母集団から外す")
    ap.add_argument("--max-odds", type=float, default=None,
                    help="この予測オッズ超の目を外す。🔴 母集団全体だと平均2068倍の"
                         "大穴に支配され、実際に買う帯の勾配が見えない")
    args = ap.parse_args()

    # 目ごとに (EV, 予測オッズ, 当たったか, 確定配当, 日付)
    legs: list[tuple[float, float, int, int, str]] = []
    n_race = 0
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("win"):
                continue
            pw = {int(k): v for k, v in r["pw"].items()}
            p3 = {int(k): v for k, v in r["p3"].items()}
            p = leg_probs(pw, p3)
            if not p:
                continue
            n_race += 1
            board = set(r["board"])
            win = r["win"]
            for leg, prob in p.items():
                if leg not in board:
                    continue
                o = r["odds"].get(leg)
                if not o or o < args.min_odds:
                    continue
                if args.max_odds and o > args.max_odds:
                    continue
                legs.append((prob * float(o), float(o), int(leg in win),
                             int(win.get(leg, 0)), r["race_date"]))
    print(f"{n_race}R / 対象の目 {len(legs):,}"
          f"（予測オッズ {args.min_odds}〜{args.max_odds or '∞'}）\n")

    legs.sort()
    q = len(legs) // args.bins
    print(f"{'EV分位':10}{'EV範囲':>18}{'目数':>10}{'的中率':>9}{'平均予測ｵｯｽﾞ':>12}"
          f"{'実現ROI':>9}{'CI95':>18}")
    for i in range(args.bins):
        seg = legs[i * q:(i + 1) * q] if i < args.bins - 1 else legs[(args.bins - 1) * q:]
        # 100円ずつ買った想定。実現 ROI = Σ確定配当 / (100 × 目数)
        pay = sum(x[3] for x in seg)
        roi = pay / (100 * len(seg))
        by_day: dict[str, list[int]] = defaultdict(list)
        for x in seg:
            by_day[x[4]].append(x[3])
        days = [(len(v), sum(v)) for v in by_day.values()]
        B = 2000
        boot = []
        for _ in range(B):
            s = [days[random.randrange(len(days))] for _ in days]
            boot.append(sum(x[1] for x in s) / (100 * sum(x[0] for x in s)))
        boot.sort()
        print(f"Q{i+1:<9}{f'{seg[0][0]:.3f}〜{seg[-1][0]:.3f}':>18}{len(seg):>10,}"
              f"{sum(x[2] for x in seg)/len(seg):>9.3%}"
              f"{st.mean([x[1] for x in seg]):>12.1f}{roi:>9.1%}"
              f"{f'[{boot[int(B*.025)]:.1%},{boot[int(B*.975)]:.1%}]':>18}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
