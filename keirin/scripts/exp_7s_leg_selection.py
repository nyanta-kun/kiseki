#!/usr/bin/env python3
"""7S の**相手選別**を設計する（2026-08-23・ユーザー指示）。

## 前提（棚卸しで確認済み）

7S は **軸2車 + 残り5車の総流し5点固定**（`RANK_7S_STAKE = unit_stake(5)`＝
2,000円/点・1レース10,000円）。**`partners_key` を持たず、相手を一切絞っていない。**
§29 の一般則「層③（相手選定）が効くのは多くの候補から少なく選ぶ商品だけ」から言うと、
5候補から5点を買っている 7S は**絞る余地が最大**（7C は既に4〜5点で余地が無かった）。

## 母集団（7S の本番ゲートを再現）

    7車 ∧ axis_sum <= 1.40 ∧ entropy <= 1.8329 ∧ wt_overlap_n ∈ {0,1}

⚠️ 統合後の 7S は 7A（axis_sum だけ不合格）・7SS（entropy だけ不合格 ∧ 二軸同ライン）・
   7A市場合意枠（overlap==2）も含むが、ここでは**素の 7S** を対象にする
   （枠ごとに性質が違うので混ぜない）。

## 測る絞り方（すべて朝に確定・オッズ非依存）

| 記号 | 中身 |
|---|---|
| `p3_min` | 相手の3着内率が閾値以上（7C 方式・`RANK_7C_LEG_P3_MIN=0.15`） |
| `topN` | `p3` 上位 N 点 |
| `gap` | 隣接する相手の `p3` 落差で打ち切り（7C 方式） |
| `line` | 軸1と同一ラインの相手を優先 |

🔴 **目的関数は「日次ROI ＋ 的中率の下限」**（`docs/product_portfolio_redesign_2026_08.md`）。
   点数を減らすと 1点あたりの賭け金が上がるので、**投資額は 10,000円のまま**。
   したがって「的中率は下がるが ROI は上がる」形になりやすい。**両方を必ず併記する。**
🔴 探索 2024-01〜2025-12 / 確認 2026-01〜06（封印 2026-07-01〜08-22 は読まない）。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis2_gap_line_confirm import load_window  # noqa: E402
from scripts.exp_trio_joint_partner import load_boards  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7S_AXIS_SUM_MAX, RANK_7S_ENTROPY_MAX, rank_7s_field_entropy,
    rank_7s_select_axis, rank_7s_wt_overlap_n, unit_stake)

SEARCH_END = "2025-12-31"
CONFIRM = ("2026-01-01", "2026-06-30")


def build(lo, hi):
    W = load_window(lo, hi)
    fin = _load_finishes(list(W))
    out = []
    for k, w in W.items():
        if k not in fin:
            continue
        sel = rank_7s_select_axis(w["pw"], w["p3"], {c: 0.0 for c in w["p3"]})
        if sel is None:
            continue
        a1, a2, asum = sel
        if asum > RANK_7S_AXIS_SUM_MAX:
            continue
        if rank_7s_field_entropy(w["p3"]) > RANK_7S_ENTROPY_MAX:
            continue
        hon = next((c for c, v in w["mark"].items() if v == 1), None)
        tai = next((c for c, v in w["mark"].items() if v == 2), None)
        if rank_7s_wt_overlap_n(a1, a2, hon, tai) not in (0, 1):
            continue
        rest = sorted((c for c in w["p3"] if c not in (a1, a2)),
                      key=lambda c: (-w["p3"][c], c))
        out.append(dict(key=k, date=w["date"], a1=a1, a2=a2, rest=rest,
                        p3=w["p3"], lg=w["lg"],
                        wins={frozenset(x) for x in winning_trifectas(fin[k])}))
    return out


def legs_of(r, rule):
    rest, p3 = r["rest"], r["p3"]
    if rule == "総流し5点(現行)":
        return rest
    if rule.startswith("上位"):
        return rest[:int(rule[2])]
    if rule.startswith("p3>="):
        t = float(rule[4:])
        return [c for c in rest if p3[c] >= t]
    if rule.startswith("落差"):
        g = float(rule[2:])
        out = [rest[0]]
        for a, b in zip(rest, rest[1:]):
            if p3[a] - p3[b] >= g:
                break
            out.append(b)
        return out
    if rule == "軸1と同ライン優先3点":
        la = r["lg"].get(r["a1"])
        same = [c for c in rest if la is not None and r["lg"].get(c) == la]
        other = [c for c in rest if c not in same]
        return (same + other)[:3]
    raise ValueError(rule)


def agg(rows, board, rule, n_days):
    st_cache = {}
    d = defaultdict(lambda: [0.0, 0.0, 0])
    n = hit = 0
    pays = []
    for r in rows:
        b = board.get(r["key"])
        if not b:
            continue
        legs = legs_of(r, rule)
        ks = [frozenset((r["a1"], r["a2"], c)) for c in legs]
        ks = [k for k in ks if k in b]
        if not ks:
            continue
        st = st_cache.setdefault(len(ks), unit_stake(len(ks)))
        pay = sum(int(b[k] * 100) * st // 100 for k in ks if k in r["wins"])
        h = any(k in r["wins"] for k in ks)
        n += 1; hit += h
        z = d[r["date"]]; z[0] += len(ks) * st; z[1] += pay; z[2] += 1
        if h:
            pays.append(pay)
    if n < 100:
        return None
    v = np.array([[x[0], x[1]] for x in d.values()], float)
    return dict(n=n, per_day=n / n_days, hit=hit / n,
                bet=v[:, 0].sum(), pay=v[:, 1].sum(),
                roi=v[:, 1].sum() / v[:, 0].sum(), days=d,
                med=float(np.median(pays)) if pays else 0.0,
                avg_legs=np.mean([len(legs_of(r, rule)) for r in rows]))


def ci(days_a, days_b, B=4000, seed=211):
    ks = sorted(set(days_a) & set(days_b))
    v = np.array([[days_a[k][0], days_a[k][1], days_b[k][1]] for k in ks], float)
    rng = np.random.default_rng(seed)
    i = rng.integers(0, len(v), size=(B, len(v)))
    t = v[i, 0].sum(1)
    z = np.sort(v[i, 2].sum(1) / t - v[i, 1].sum(1) / t)
    return z[int(B * .025)], z[int(B * .975)]


RULES = ["総流し5点(現行)", "上位4", "上位3", "上位2",
         "p3>=0.15", "p3>=0.25", "p3>=0.35",
         "落差0.10", "落差0.15", "軸1と同ライン優先3点"]


def main() -> int:
    ap = argparse.ArgumentParser()
    args = ap.parse_args()
    for label, (lo, hi) in (("探索 2024-01〜2025-12", ("2024-01-01", SEARCH_END)),
                            ("確認 2026-01〜06", CONFIRM)):
        rows = build(lo, hi)
        board = load_boards([r["key"] for r in rows])
        nd = len({r["date"] for r in rows})
        print(f"\n===== {label} ・ 7S 母集団 {len(rows):,}R / {nd}日 =====")
        print(f"{'相手の絞り方':>22}{'平均点':>7}{'件/日':>7}{'的中%':>8}"
              f"{'ROI':>8}{'（対現行）':>24}{'中央払戻':>10}")
        base = agg(rows, board, RULES[0], nd)
        for rule in RULES:
            a = agg(rows, board, rule, nd)
            if not a:
                continue
            if rule == RULES[0]:
                print(f"{rule:>22}{a['avg_legs']:>7.2f}{a['per_day']:>7.2f}"
                      f"{a['hit']:>8.2%}{a['roi']:>8.1%}{'':>24}{a['med']:>10,.0f}")
                continue
            lo_, hi_ = ci(base["days"], a["days"])
            f = "🟢" if lo_ > 0 else ("🔴" if hi_ < 0 else "")
            c = f"{(a['roi']-base['roi'])*100:+.1f}pt[{lo_*100:+.1f},{hi_*100:+.1f}]{f}"
            print(f"{rule:>22}{a['avg_legs']:>7.2f}{a['per_day']:>7.2f}"
                  f"{a['hit']:>8.2%}{a['roi']:>8.1%}{c:>24}{a['med']:>10,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
