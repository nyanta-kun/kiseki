#!/usr/bin/env python3
"""三連複: レース内の**実力プロフィールの形**で相手（順位5）の妙味が変わるか。

## ユーザー仮説（2026-08-23）

> 実力的に明確に3車が抜けているレースよりは、**2車のみが抜けていて他が混戦**な
> レースの方が指数5位が3着に来る可能性が高く、さらに**抜けている2車以外のうち
> 最下位も実力が下に外れている**と、より5位が来る確率は上がる

🔴 前回「レース選別は効かない」と結論したが、測ったのは `axis_sum` / `entropy` /
   印一致 / 決勝系 といった**集約量**だけだった。
   - `axis_sum` = p3[1]+p3[2] は**3位との差を見ていない**
   - `entropy` は単一の集約値なので「3車抜け」と「2車抜け＋残り混戦」を**区別できない**
   前回の結論は「**試した特徴では**効かない」であって「レース選別が不可能」ではない。

## 検証する形（すべて 3着内率 p3 のレース内順位に対する差）

| 特徴 | 定義 | 仮説の向き（相手=順位5 の ROI） |
|---|---|---|
| `gap23` | p3[2] − p3[3] | 大きいほど **上**（2車抜け） |
| `gap34` | p3[3] − p3[4] | 大きいほど **下**（3車抜け） |
| `gap67` | p3[6] − p3[7] | 大きいほど **上**（最下位が脱落） |
| `spread36` | p3[3] − p3[6] | 小さいほど **上**（相手候補が団子） |

🔴 **方向を事前に決めてあるのが要点。** 掃引して最良セルを拾うのではなく、
   **予測どおりの符号が探索窓と確認窓の両方で出るか**を見る。
   符号が反転したら、その時点で不採用（良いほうの窓を選び直さないこと）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485

#: 仮説の向き。`+1` = 値が大きいほど ROI が上がるはず
DIRECTION = {"gap23": +1, "gap34": -1, "gap67": +1, "spread36": -1}


def load(cache: str, partners: list[int]):
    rows = []
    with open(cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win"):
                rows.append(r)
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    keys = [r["race_key"] for r in rows]
    board: dict[str, dict] = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                    "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                    (keys[i:i + 2000],))
        for rk, c, o in cur.fetchall():
            s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(s) == 3:
                board[rk][s] = float(o)

    rec = []
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        order = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        if len(order) < 7:
            continue
        v = [p3[c] for c in order]          # 降順の3着内率
        a1, a2 = order[0], order[1]
        legs = [frozenset((a1, a2, order[i - 1])) for i in partners
                if frozenset((a1, a2, order[i - 1])) in bd]
        if not legs:
            continue
        stake = unit_stake(len(legs))
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        pay = next((int(bd[k] * 100) * stake // 100 for k in legs if k in wins), 0)
        rec.append(dict(date=r["race_date"], bet=stake * len(legs), pay=pay,
                        # 🔴 順位は 1-indexed。v[1] が順位2、v[2] が順位3
                        gap23=v[1] - v[2], gap34=v[2] - v[3],
                        gap67=v[5] - v[6], spread36=v[2] - v[5],
                        top3_in=int(order[4] in {x for w in wins for x in w})))
    return rec


def roi_ci(seg, B=1500):
    by: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for x in seg:
        a = by[x["date"]]
        a[0] += x["bet"]
        a[1] += x["pay"]
    v = list(by.values())
    bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
    idx = np.random.randint(0, len(v), size=(B, len(v)))
    b = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
    return pay.sum() / bet.sum(), b[int(B * .025)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--partners", default="5")
    ap.add_argument("--split", default="2026-05-01")
    args = ap.parse_args()

    np.random.seed(141)
    partners = [int(x) for x in args.partners.split(",")]
    rec = load(args.cache, partners)
    sel = [x for x in rec if x["date"] < args.split]
    conf = [x for x in rec if x["date"] >= args.split]
    print(f"相手=順位{args.partners} / {len(rec):,}R"
          f"（探索 {len(sel):,} / 確認 {len(conf):,}）  壁 {PAYOUT_RATE:.2%}\n")

    ra, la = roi_ci(sel); rb, lb = roi_ci(conf)
    print(f"{'全レース':>28}{len(sel):>8,}{ra:>8.1%}{la:>8.1%}"
          f"{len(conf):>8,}{rb:>8.1%}{lb:>8.1%}")
    print(f"\n{'特徴 / 分位':>28}{'探索:R':>8}{'ROI':>8}{'CI下限':>8}"
          f"{'確認:R':>8}{'ROI':>8}{'CI下限':>8}{'順位5の3着内率':>14}")
    for feat, direction in DIRECTION.items():
        qs = np.quantile([x[feat] for x in sel], [.25, .5, .75])
        cells = []
        for i in range(4):
            lo = -9 if i == 0 else qs[i - 1]
            hi = 9 if i == 3 else qs[i]
            a = [x for x in sel if lo <= x[feat] < hi]
            b = [x for x in conf if lo <= x[feat] < hi]
            if len(a) < 400 or len(b) < 400:
                continue
            r1, l1 = roi_ci(a); r2, l2 = roi_ci(b)
            t3 = np.mean([x["top3_in"] for x in a + b])
            cells.append((i, r1, l1, r2, l2, len(a), len(b), t3))
            mk = " 🟢両窓で壁超" if l1 > PAYOUT_RATE and l2 > PAYOUT_RATE else ""
            print(f"{f'{feat} Q{i+1}':>28}{len(a):>8,}{r1:>8.1%}{l1:>8.1%}"
                  f"{len(b):>8,}{r2:>8.1%}{l2:>8.1%}{t3:>14.2%}{mk}")
        if len(cells) >= 2:
            # 🔴 仮説の向きどおりか（Q4−Q1 の符号）。両窓で一致して初めて意味がある
            d1 = (cells[-1][1] - cells[0][1]) * direction
            d2 = (cells[-1][3] - cells[0][3]) * direction
            ok = "🟢 両窓で仮説どおり" if d1 > 0 and d2 > 0 else (
                 "❌ 符号が窓で反転" if d1 * d2 < 0 else "❌ 仮説と逆")
            print(f"{'':>28}→ 仮説の向き（{'大きいほど上' if direction>0 else '大きいほど下'}）: "
                  f"探索 {d1*100:+.1f}pt / 確認 {d2*100:+.1f}pt  {ok}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
