#!/usr/bin/env python3
"""軸2 は2着に来るのか3着に来るのか（＝現行の形が捨てている量）。

現行 7T1 は `1着=軸1 / 2着=軸2 / 3着=相手` なので、**軸2が3着に来た二軸的中を
丸ごと落としている**。ユーザー提案（2026-08-22）「2・3着を軸2として相手を広げる」は
そこを拾う案なので、まず落としている量と、そのときの配当水準を実測する。

🔴 **「拾えば増える」とは限らない。** 予算1万円は固定なので、点数を2倍にすると
1点あたりが半分になり払戻も半分になる。理論上 `P(払戻>=T)` は点数に依存しない
（`exp_tf_shape_eval.py` の docstring）。本スクリプトが答えるのは
**「拾える的中がどれだけあり、その配当がどの帯か」**だけで、採否は形の比較で決める。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_tf_shape_eval import axis_simple, load, pops  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--pop", default="全7車",
                    choices=("全7車", "別ライン", "決勝系×別ライン"))
    args = ap.parse_args()

    rows = load(args.cache)
    buckets: dict[str, list[int]] = {"軸2=2着": [], "軸2=3着": [], "軸2=着外": []}
    n_a1_win = n_total = 0
    for row in rows:
        p3 = {int(k): v for k, v in row["p3"].items()}
        pw = {int(k): v for k, v in row["pw"].items()}
        if not pops(row, p3)[args.pop]:
            continue
        n_total += 1
        a1, a2 = axis_simple(p3, pw)
        # 同着があるので当たり目は複数。**軸1が1着の目**だけを見る。
        cand = [(c, o) for c, o in row["win"].items()
                if int(c.split("-")[0]) == a1]
        if not cand:
            continue
        n_a1_win += 1
        # 軸2 の位置は当たり目ごとに決まる。同着で複数あるときは最も良い位置を採る
        pos = "軸2=着外"
        odds = 0
        for c, o in cand:
            parts = [int(x) for x in c.split("-")]
            if parts[1] == a2:
                pos, odds = "軸2=2着", o
                break
            if parts[2] == a2:
                pos, odds = "軸2=3着", o
        if pos == "軸2=着外":
            odds = cand[0][1]
        buckets[pos].append(odds)

    print(f"母集団 {args.pop}: {n_total}R / 軸1が1着 {n_a1_win}R "
          f"({n_a1_win / n_total:.1%})\n")
    print(f"{'':10}{'件':>7}{'軸1勝ち中':>10}{'全体比':>8}"
          f"{'配当中央':>10}{'配当p25':>10}{'配当p75':>10}")
    for k, v in buckets.items():
        if not v:
            continue
        s = sorted(v)
        print(f"{k:10}{len(v):>7}{len(v) / n_a1_win:>10.1%}{len(v) / n_total:>8.2%}"
              f"{st.median(s):>10,}{s[len(s) // 4]:>10,}{s[3 * len(s) // 4]:>10,}")
    two, three = len(buckets["軸2=2着"]), len(buckets["軸2=3着"])
    if two + three:
        print(f"\n二軸的中のうち軸2が3着だった割合: {three / (two + three):.1%}"
              f"（＝現行 F1 が構造的に落としている分）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
