#!/usr/bin/env python3
"""二軸的中率そのものを上げられるか — **ペアの選び方**を比べる。

## ユーザー指摘（2026-08-23）

> 相手5位の話は二軸が的中できる上での話。現状は二軸的中の精度が低いので、
> そこを上げる必要がある

🔴 **そのとおり。** 三連複 {軸1, 軸2, 相手} は**両軸が3着内に入らないと絶対に当たらない**
ので、二軸的中率が商品の天井。これまで測ったのは「軸2をどの車にするか」だけで、
**ペアとして同時に3着内へ入りやすい組を選ぶ**という発想を試していなかった。

## 現行の何が足りないか

現行は **限界確率（p3）の上位2車**を機械的に採る。これは
`argmax p3_i + argmax p3_j` であって `argmax P(i と j がともに3着内)` ではない。
3着の枠は3つしかないので**車どうしは競合する**（1車が入れば他の枠が減る）し、
ライン構造で同時に来やすい組・来にくい組がある。

実測（2026年窓）: **軸1と軸2が同ライン 二軸的中 59.8% / 別ライン 45.8%** ＝ 14pt 差。
現行はこれを一切使っていない。

## 比べるペアの選び方

| 記号 | 規則 |
|---|---|
| `P0` | p3 上位2車（**現行**） |
| `P1` | p3 の積が最大のペア（＝実質 P0 と同じはず。検算用） |
| `P2` | **同ラインのペアを優先**（無ければ P0 へ）|
| `P3` | 同ラインかつ p3 上位、を 21ペアから argmax |
| `P4` | ライン先頭＋その番手（隊列として繋がっている組） |
| `P5` | 逆向き: **別ライン**を優先（方向確認用） |

🔴 **逆向きを必ず入れる。** 何を選んでも上がるなら、それはペアの選び方ではなく
   別の要因が動いただけ。

検証は **2024-01〜2025-12 の独立窓（46,359R）**。
⚠️ 二軸的中が上がっても ROI が上がるとは限らない（同ラインは配当も安い）。
   **精度と収益は分けて出す。**
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485


def pick_pair(rule, order, p3, lg, lp):
    """(軸1, 軸2) を返す。order は p3 降順の車番列。"""
    a1 = order[0]
    if rule == "P0 p3上位2(現行)":
        return a1, order[1]
    pairs = list(itertools.combinations(order, 2))
    if rule == "P1 p3の積が最大":
        return max(pairs, key=lambda ab: p3[ab[0]] * p3[ab[1]])
    if rule == "P2 同ライン優先(軸1固定)":
        same = [c for c in order[1:] if lg.get(c) is not None and lg.get(c) == lg.get(a1)]
        return (a1, same[0]) if same else (a1, order[1])
    if rule == "P3 同ライン×p3最大":
        same = [ab for ab in pairs
                if lg.get(ab[0]) is not None and lg.get(ab[0]) == lg.get(ab[1])]
        if same:
            return max(same, key=lambda ab: p3[ab[0]] * p3[ab[1]])
        return a1, order[1]
    if rule == "P4 ライン先頭＋番手":
        cand = [ab for ab in pairs
                if lg.get(ab[0]) is not None and lg.get(ab[0]) == lg.get(ab[1])
                and {str(lp.get(ab[0])), str(lp.get(ab[1]))} == {"1", "2"}]
        if cand:
            return max(cand, key=lambda ab: p3[ab[0]] * p3[ab[1]])
        return a1, order[1]
    if rule == "P5 別ライン優先(逆向き)":
        diff = [c for c in order[1:] if lg.get(c) != lg.get(a1)]
        return (a1, diff[0]) if diff else (a1, order[1])
    raise ValueError(rule)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/trio_rank_cache.jsonl")
    args = ap.parse_args()
    np.random.seed(311)

    rows = [json.loads(x) for x in open(args.cache)]
    keys = [r["race_key"] for r in rows]
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
    line: dict[str, dict] = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, frame_no, line_group, line_pos from keirin.wt_entries "
                    "where race_key = any(%s)", (keys[i:i + 2000],))
        for rk, fn, g, p in cur.fetchall():
            line[rk][int(fn)] = (g, p)
    board: dict[str, dict] = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                    "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                    (keys[i:i + 2000],))
        for rk, c, o in cur.fetchall():
            s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(s) == 3:
                board[rk][s] = float(o)
    fins = _load_finishes(keys)
    print(f"独立窓 {len(rows):,}R / ライン {len(line):,} / 板 {len(board):,}\n")

    RULES = ["P0 p3上位2(現行)", "P1 p3の積が最大", "P2 同ライン優先(軸1固定)",
             "P3 同ライン×p3最大", "P4 ライン先頭＋番手", "P5 別ライン優先(逆向き)"]
    res = {r: [] for r in RULES}
    for r in rows:
        o3 = fins.get(r["race_key"]); bd = board.get(r["race_key"])
        lnr = line.get(r["race_key"])
        if not o3 or not bd or not lnr:
            continue
        order = r["order"]
        if len(order) < 7:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        lg = {c: v[0] for c, v in lnr.items()}
        lp = {c: v[1] for c, v in lnr.items()}
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        top3 = {c for w in wins for c in w}
        for rule in RULES:
            a1, a2 = pick_pair(rule, order, p3, lg, lp)
            rest = [c for c in order if c not in (a1, a2)]
            # 相手は「軸を除いた p3 上位から3番目」＝従来の順位5相当
            partner = rest[2] if len(rest) >= 3 else None
            k = frozenset((a1, a2, partner)) if partner else None
            pay = 0
            bet = unit_stake(1)
            if k and k in bd:
                pay = int(bd[k] * 100) * bet // 100 if k in wins else 0
            res[rule].append((r["race_date"], int(a1 in top3 and a2 in top3),
                              bet if k in bd else 0, pay,
                              int(lg.get(a1) is not None and lg.get(a1) == lg.get(a2))))

    def ci(vals, B=2000):
        by = defaultdict(lambda: [0.0, 0.0])
        for d, _, b, p, _ in vals:
            a = by[d]; a[0] += b; a[1] += p
        v = [x for x in by.values() if x[0] > 0]
        bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
        idx = np.random.randint(0, len(v), size=(B, len(v)))
        bs = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
        return pay.sum() / bet.sum(), bs[int(B * .025)]

    print(f"{'ペアの選び方':24}{'R数':>9}{'二軸的中':>9}{'同ライン率':>10}"
          f"{'相手5位の的中':>12}{'ROI':>8}{'CI下限':>8}")
    base = None
    for rule in RULES:
        v = res[rule]
        both = np.mean([x[1] for x in v])
        if base is None:
            base = both
        r, lo = ci(v)
        hit = np.mean([1 if x[3] > 0 else 0 for x in v if x[2] > 0])
        mk = f"  Δ{(both-base)*100:+.1f}pt" if rule != RULES[0] else ""
        print(f"{rule:24}{len(v):>9,}{both:>9.2%}{np.mean([x[4] for x in v]):>10.1%}"
              f"{hit:>12.2%}{r:>8.1%}{lo:>8.1%}{mk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
