#!/usr/bin/env python3
"""相手を「1番手と同程度の確からしさ」まで買う（点数はレースごとに可変）。

## ユーザー指示（2026-08-23）

> 相手総流しが最良なことはない。総流しだと的中は1件で残り4件はハズレ。
> **買い目に集中、ただし同程度の信頼度で点数増えるのはやむなし**

🔴 総流しは ROI が同じでも設計として劣る。**同じ ROI ということは死んだ4点が
   公正価格で買われているだけ**＝払戻を薄めているだけで、一撃力を失っている。

## 規則

相手候補（軸2車を除く5車）を指標の降順に並べ、**1番手の α 倍以上**のものまで買う:

    買う = { c : score(c) >= α × score(1番手) }        α ∈ [0.5, 1.0]

α=1.0 なら常に1点（同値のときだけ増える）。α が小さいほど点数が増える。
🔴 **固定点数ではなく「同程度かどうか」で決まる**のが要点。拮抗しているレースでは
   自然に点数が増え、1車が抜けているレースでは1点になる。

## 並べる指標

| 記号 | score | 意味 |
|---|---|---|
| `prob` | 三連複の的中確率（位置別合成PLを6順列合算） | 当たりそうな順 |
| `ev` | 的中確率 × 予測オッズ | 割安な順 |

⚠️ この2つは**まったく違う相手を選ぶ**（prob なら順位3、ROI 実測が良いのは順位5）。
   両方測って比べる。

判定は探索窓（〜2026-04）/ 確認窓（2026-05〜）。日次上限は置かない
（[[keirin_trio_partner_and_axis2_2026_08_23]]: 厳選を測るときは上限を外す）。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_leg_prob_heads import strengths  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485


def trio_probs(pw, p3) -> dict[frozenset, float]:
    """全35通りの三連複確率（位置別合成 PL を6順列ぶん合算）。"""
    s = [strengths(pw, p3, a) for a in (1.0, 0.5, 0.0)]
    s1 = sum(s[0].values())
    cars = list(pw)
    out: dict[frozenset, float] = defaultdict(float)
    for x, y, z in itertools.permutations(cars, 3):
        d2 = sum(s[1][c] for c in cars if c != x)
        d3 = sum(s[2][c] for c in cars if c not in (x, y))
        if d2 <= 0 or d3 <= 0:
            continue
        out[frozenset((x, y, z))] += (s[0][x] / s1) * (s[1][y] / d2) * (s[2][z] / d3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    args = ap.parse_args()
    np.random.seed(261)

    rows = []
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win"):
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

    R = []
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        pw = {int(k): v for k, v in r["pw"].items()}
        if len(p3) < 7 or len(pw) < 7:
            continue
        o = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        a1, a2 = o[0], o[1]
        tp = trio_probs(pw, p3)
        cands = []
        for c in o[2:]:
            k = frozenset((a1, a2, c))
            if k not in bd:
                continue
            pr = tp.get(k, 0.0)
            cands.append(dict(key=k, prob=pr, ev=pr * bd[k], rank=o.index(c) + 1))
        if not cands:
            continue
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        R.append(dict(date=r["race_date"], bd=bd, cands=cands, wins=wins))

    print(f"{len(R):,}R / {len({x['date'] for x in R})}日（日次上限なし）"
          f"  壁 {PAYOUT_RATE:.2%}\n")

    def run(order, alpha):
        out = []
        for x in R:
            cs = sorted(x["cands"], key=lambda c: -c[order])
            top = cs[0][order]
            if top <= 0:
                continue
            take = [c for c in cs if c[order] >= alpha * top]
            stake = unit_stake(len(take))
            pay = next((int(x["bd"][c["key"]] * 100) * stake // 100
                        for c in take if c["key"] in x["wins"]), 0)
            out.append((x["date"], stake * len(take), pay, len(take),
                        st.mean([c["rank"] for c in take])))
        return out

    def report(seg, B=2000):
        by = defaultdict(lambda: [0.0, 0.0])
        for d, b, p, n, _ in seg:
            a = by[d]; a[0] += b; a[1] += p
        v = list(by.values())
        bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
        idx = np.random.randint(0, len(v), size=(B, len(v)))
        bs = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
        rois = pay / bet
        pl = sorted(p for _, _, p, _, _ in seg if p > 0)
        return dict(roi=pay.sum() / bet.sum(), lo=bs[int(B * .025)],
                    hit=sum(1 for s in seg if s[2] > 0) / len(seg),
                    pts=st.mean([s[3] for s in seg]),
                    rank=st.mean([s[4] for s in seg]),
                    over=float((rois >= 1).mean()), zero=float((rois == 0).mean()),
                    med=(st.median(pl) if pl else 0), n=len(seg))

    for order in ("prob", "ev"):
        print(f"===== 相手を {order} の降順に並べ、1番手の α 倍以上まで買う =====")
        print(f"{'α':>6}{'窓':>5}{'R数':>8}{'平均点':>7}{'平均順位':>9}{'的中%':>8}"
              f"{'ROI':>8}{'CI下限':>8}{'100%超':>8}{'0円日':>7}{'中央払戻':>10}")
        for alpha in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.3, 0.0):
            for wn, f in (("探索", lambda d: d < args.split),
                          ("確認", lambda d: d >= args.split)):
                seg = [s for s in run(order, alpha) if f(s[0])]
                if len(seg) < 500:
                    continue
                k = report(seg)
                mk = " 🟢" if k["lo"] > PAYOUT_RATE else ""
                print(f"{alpha if wn == '探索' else '':>6}{wn:>5}{k['n']:>8,}"
                      f"{k['pts']:>7.2f}{k['rank']:>9.2f}{k['hit']:>8.2%}"
                      f"{k['roi']:>8.1%}{k['lo']:>8.1%}{k['over']:>8.1%}"
                      f"{k['zero']:>7.1%}{k['med']:>10,.0f}{mk}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
