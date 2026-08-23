#!/usr/bin/env python3
"""軸2 の精度は上げられるか（選び方の比較 + 飛ぶ条件の特定）。

## ユーザー指摘（2026-08-23）

> 二軸の精度を上げる検討も別途必要。現在も軸2が外れるケースが多くあり、
> 条件による軸2の精度は現在以上に上げられる

現状の軸2:
  - 7C 系: `p3 順位2` の**素取り（1ヘッド）**
  - 7S 系: 3ヘッド合成 `z(p3) − 0.3 × z(bad)`（`RANK_AXIS2_BAD_WEIGHT`）

7C 系が1ヘッドしか使っていないので、4ヘッド（p3 / pw / top2 / bad）を
使った選び方と比べる。

## 測るもの

- **軸2の3着内率**（ユーザーの言う「外れる」の直接の量）
- **二軸的中率**（軸1・軸2がともに3着内。7C/7S が実際に買っている形）
- **商品 ROI**（軸2車＋相手=p3順位5 の1点買い）

🔴 **軸2を替えると「順位5」の定義も動く**（順位は p3 の並びで決めるが、
   軸2 が順位2 以外になると相手候補の並びがずれる）。
   ここでは**相手＝軸2を除いた p3 上位から数えて3番目**（＝従来の順位5相当）に揃える。

🔴 **逆向きの腕を必ず入れる**（`bad` を足す向きを反転）。何を入れても改善するなら
   選び方ではなく母集団が動いただけ。
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


def _z(d):
    v = list(d.values()); m = st.mean(v); s = st.pstdev(v) or 1e-9
    return {k: (x - m) / s for k, x in d.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    args = ap.parse_args()
    np.random.seed(171)

    rows = []
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win") and r.get("bad") and r.get("top2"):
                rows.append(r)
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
    keys = [r["race_key"] for r in rows]; board = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                    "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                    (keys[i:i + 2000],))
        for rk, c, o in cur.fetchall():
            s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(s) == 3:
                board[rk][s] = float(o)
    print(f"{len(rows):,}R（4ヘッド）\n")

    ARMS = ["p3順位2(7C現行)", "3ヘッド w=0.3(7S現行)", "3ヘッド w=0.5",
            "3ヘッド w=1.0", "top2ヘッド", "pwヘッド", "逆向き w=-0.3"]
    rec = {a: [] for a in ARMS}
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        pw = {int(k): v for k, v in r["pw"].items()}
        bad = {int(k): v for k, v in r["bad"].items()}
        t2 = {int(k): v for k, v in r["top2"].items()}
        if min(len(p3), len(pw), len(bad), len(t2)) < 7:
            continue
        order = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        a1 = order[0]
        cand = [c for c in order if c != a1]
        zp, zb = _z({c: p3[c] for c in cand}), _z({c: bad[c] for c in cand})
        picks = {
            "p3順位2(7C現行)": cand[0],
            "3ヘッド w=0.3(7S現行)": max(cand, key=lambda c: zp[c] - 0.3 * zb[c]),
            "3ヘッド w=0.5": max(cand, key=lambda c: zp[c] - 0.5 * zb[c]),
            "3ヘッド w=1.0": max(cand, key=lambda c: zp[c] - 1.0 * zb[c]),
            "top2ヘッド": max(cand, key=lambda c: t2[c]),
            "pwヘッド": max(cand, key=lambda c: pw[c]),
            "逆向き w=-0.3": max(cand, key=lambda c: zp[c] + 0.3 * zb[c]),
        }
        top3 = {x for w in r["win"] for x in w.split("-")}
        top3 = {int(x) for x in top3}
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        for arm, a2 in picks.items():
            rest = [c for c in order if c not in (a1, a2)]
            if len(rest) < 3:
                continue
            partner = rest[2]              # 従来の「順位5」相当
            key = frozenset((a1, a2, partner))
            if key not in bd:
                continue
            stake = unit_stake(1)
            rec[arm].append(dict(date=r["race_date"], bet=stake,
                                 pay=int(bd[key] * 100) * stake // 100
                                 if key in wins else 0,
                                 a2_in=int(a2 in top3),
                                 both=int(a1 in top3 and a2 in top3),
                                 hit=int(key in wins)))

    def agg(seg, B=1500):
        by = defaultdict(lambda: [0.0, 0.0])
        for x in seg:
            a = by[x["date"]]; a[0] += x["bet"]; a[1] += x["pay"]
        v = list(by.values())
        bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
        idx = np.random.randint(0, len(v), size=(B, len(v)))
        b = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
        return (np.mean([x["a2_in"] for x in seg]), np.mean([x["both"] for x in seg]),
                np.mean([x["hit"] for x in seg]), pay.sum() / bet.sum(), b[int(B * .025)])

    print(f"{'軸2の選び方':22}{'窓':>5}{'軸2の3着内':>10}{'二軸的中':>9}"
          f"{'商品的中':>9}{'ROI':>8}{'CI下限':>8}")
    for arm in ARMS:
        for wn, f in (("探索", lambda x: x["date"] < args.split),
                      ("確認", lambda x: x["date"] >= args.split)):
            seg = [x for x in rec[arm] if f(x)]
            if len(seg) < 500:
                continue
            a2i, both, hit, roi, lo = agg(seg)
            mk = " 🟢" if lo > PAYOUT_RATE else ""
            print(f"{arm if wn == '探索' else '':22}{wn:>5}{a2i:>10.2%}{both:>9.2%}"
                  f"{hit:>9.2%}{roi:>8.1%}{lo:>8.1%}{mk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
