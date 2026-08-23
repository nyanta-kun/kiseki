#!/usr/bin/env python3
"""三連複の相手選別に**市場直交ヘッド（大敗率）**を使えるか。

## なぜこれを試すのか

`p3` から作ったレース形状シグナルは、順位5の3着内率を 6.6%→9.7%（相対1.46倍）
動かせるが **ROI には残らない**。的中率が上がる帯ではオッズがちょうど同じだけ
下がるため（`exp_trio_race_shape.py` / 実測 積 0.84〜1.04 で一定）。
これはこのリポジトリの一般則そのもの:
**「市場と同じ向きの分類器は、精度がどれだけ高くても ROI にならない」**（7H2 の否定結果）。

残る道は**市場が値付けしていない予測**を使うこと。競輪で唯一それが実証されているのは
**大敗率ヘッド `lgbm_wt_bad`**（本命が4着以下に飛ぶ・AUC 0.6848。7H1 が壁を超えた源）。

## 比べる相手の選び方（軸2車＝p3上位2 は固定・1点買い）

| 記号 | 相手の選び方 |
|---|---|
| `p3-5` | p3 順位5（現行の最良・ROI 83.5%） |
| `bad-min` | 相手候補（p3 順位3〜7）のうち **大敗率が最小**の車 |
| `blend` | `z(p3) − w·z(bad)` が最大の相手（w を掃引） |
| `bad-max` | 大敗率が最大（**逆向き**。方向に意味があるかの確認用） |

🔴 **逆向きを必ず入れる。** 何を選んでも改善するなら、それは選び方ではなく
   母集団が動いただけ。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485


def _z(d: dict[int, float]) -> dict[int, float]:
    v = list(d.values())
    m = st.mean(v)
    s = st.pstdev(v) or 1e-9
    return {k: (x - m) / s for k, x in d.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    args = ap.parse_args()
    np.random.seed(161)

    rows = []
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win") and r.get("bad"):
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
    print(f"{len(rows):,}R（bad ヘッドあり）\n")

    ARMS = ["p3-5", "bad-min", "bad-max"] + [f"blend w={w}" for w in (0.3, 0.5, 1.0)]
    rec: dict[str, list] = {a: [] for a in ARMS}
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        bad = {int(k): v for k, v in r["bad"].items()}
        if len(bad) < 7:
            continue
        order = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        if len(order) < 7:
            continue
        a1, a2 = order[0], order[1]
        cands = order[2:]                       # 相手候補＝p3 順位3〜7
        zp, zb = _z({c: p3[c] for c in cands}), _z({c: bad[c] for c in cands})
        picks = {
            "p3-5": order[4],
            "bad-min": min(cands, key=lambda c: bad[c]),
            "bad-max": max(cands, key=lambda c: bad[c]),
        }
        for w in (0.3, 0.5, 1.0):
            picks[f"blend w={w}"] = max(cands, key=lambda c: zp[c] - w * zb[c])
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        for arm, c in picks.items():
            key = frozenset((a1, a2, c))
            if key not in bd:
                continue
            stake = unit_stake(1)
            rec[arm].append(dict(date=r["race_date"], bet=stake,
                                 pay=int(bd[key] * 100) * stake // 100
                                 if key in wins else 0,
                                 hit=1 if key in wins else 0, odds=bd[key]))

    def roi_ci(seg, B=1500):
        by = defaultdict(lambda: [0.0, 0.0])
        for x in seg:
            a = by[x["date"]]
            a[0] += x["bet"]; a[1] += x["pay"]
        v = list(by.values())
        bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
        idx = np.random.randint(0, len(v), size=(B, len(v)))
        b = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
        return pay.sum() / bet.sum(), b[int(B * .025)]

    print(f"{'相手の選び方':16}{'探索:R':>8}{'的中%':>7}{'ROI':>8}{'CI下限':>8}"
          f"{'確認:R':>8}{'的中%':>7}{'ROI':>8}{'CI下限':>8}{'ｵｯｽﾞ中央':>9}")
    for arm in ARMS:
        s = [x for x in rec[arm] if x["date"] < args.split]
        c = [x for x in rec[arm] if x["date"] >= args.split]
        if len(s) < 500 or len(c) < 500:
            continue
        ra, la = roi_ci(s); rb, lb = roi_ci(c)
        mk = " 🟢両窓で壁超" if la > PAYOUT_RATE and lb > PAYOUT_RATE else ""
        print(f"{arm:16}{len(s):>8,}{np.mean([x['hit'] for x in s]):>7.2%}{ra:>8.1%}{la:>8.1%}"
              f"{len(c):>8,}{np.mean([x['hit'] for x in c]):>7.2%}{rb:>8.1%}{lb:>8.1%}"
              f"{np.median([x['odds'] for x in s + c]):>9.1f}{mk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
