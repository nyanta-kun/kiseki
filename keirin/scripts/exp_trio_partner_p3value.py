#!/usr/bin/env python3
"""相手を「順位」ではなく **3着内率の値** で切り直す。

## ユーザー指摘（2026-08-23）

> 評価を単純な順位のみで語っているが、3着内率の（値を見ていない）

🔴 **そのとおり。** 同じ「順位5」でも
`[0.90,0.80,0.50,0.40,0.35,0.30,0.25]` のレースと
`[0.60,0.55,0.50,0.48,0.46,0.44,0.40]` のレースはまったく別物で、
順位はその違いを潰す。**順位5の優位が独立窓で消えたのも、
順位という粗い量で括ったせいかもしれない。**

## 切り方（すべて朝8:00 に手に入る）

| 記号 | 量 | 意味 |
|---|---|---|
| `p3_abs` | 相手の3着内率そのもの | 絶対的な強さ |
| `p3_rel` | 相手 ÷ 軸2 | 軸2に対してどれだけ劣るか |
| `p3_gap_up` | 軸2 − 相手 | 軸2との差（絶対） |
| `p3_gap_dn` | 相手 − 次点 | 次の車をどれだけ引き離しているか |

🔴 **探索 2024-01〜2025-12（46,359R）／確認 2026-01〜08（14,941R）。**
   順位ベースの結論が独立窓で崩れた前例があるので、必ず年をまたいで確認する。
🔴 判定は事前登録: 日ブロック bootstrap の CI 下限 > 払戻率 74.85%、
   **かつ探索窓で選んだ帯が確認窓でも壁を超えること**。
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

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485


def collect(rows, board, fins):
    """1レースにつき相手5車ぶんの行（軸2車は p3 上位2で固定）。"""
    out = []
    for r in rows:
        bd = board.get(r["key"]); o3 = fins.get(r["key"])
        if not bd or not o3:
            continue
        o = r["order"]; p3 = r["p3"]
        if len(o) < 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        a1, a2 = o[0], o[1]
        vals = [p3[c] for c in o]
        for i in range(2, 7):
            c = o[i]
            k = frozenset((a1, a2, c))
            if k not in bd:
                continue
            nxt = vals[i + 1] if i + 1 < len(vals) else 0.0
            out.append(dict(
                date=r["date"], rank=i + 1, bet=unit_stake(1),
                pay=int(bd[k] * 100) * unit_stake(1) // 100 if k in wins else 0,
                p3_abs=p3[c], p3_rel=p3[c] / max(p3[a2], 1e-9),
                p3_gap_up=p3[a2] - p3[c], p3_gap_dn=p3[c] - nxt,
                odds=bd[k]))
    return out


def roi_ci(seg, B=2000):
    by = defaultdict(lambda: [0.0, 0.0])
    for x in seg:
        a = by[x["date"]]; a[0] += x["bet"]; a[1] += x["pay"]
    v = list(by.values())
    bet = np.array([x[0] for x in v]); pay = np.array([x[1] for x in v])
    idx = np.random.randint(0, len(v), size=(B, len(v)))
    b = np.sort(pay[idx].sum(1) / bet[idx].sum(1))
    return pay.sum() / bet.sum(), b[int(B * .025)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--confirm", default="data/exp/tf_shape_cache4.jsonl")
    args = ap.parse_args()
    np.random.seed(371)

    S = [dict(key=r["race_key"], date=r["race_date"], order=r["order"],
              p3={int(k): v for k, v in r["p3"].items()})
         for r in map(json.loads, open(args.search))]
    C = []
    with open(args.confirm) as f:
        for x in f:
            r = json.loads(x)
            if not r.get("win"):
                continue
            p3 = {int(k): v for k, v in r["p3"].items()}
            C.append(dict(key=r["race_key"], date=r["race_date"], p3=p3,
                          order=[c for c, _ in sorted(p3.items(),
                                                      key=lambda kv: (-kv[1], kv[0]))]))
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()

    def boards(keys):
        bd = defaultdict(dict)
        for i in range(0, len(keys), 2000):
            cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                        "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                        (keys[i:i + 2000],))
            for rk, c, o in cur.fetchall():
                s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
                if len(s) == 3:
                    bd[rk][s] = float(o)
        return bd

    ks = [r["key"] for r in S]; kc = [r["key"] for r in C]
    seg_s = collect(S, boards(ks), _load_finishes(ks))
    seg_c = collect(C, boards(kc), _load_finishes(kc))
    print(f"探索 {len(seg_s):,}行（{len(S):,}R） / 確認 {len(seg_c):,}行（{len(C):,}R）")
    print(f"壁 {PAYOUT_RATE:.2%}\n")

    for feat in ("p3_abs", "p3_rel", "p3_gap_up", "p3_gap_dn"):
        qs = np.quantile([x[feat] for x in seg_s], [.2, .4, .6, .8])
        print(f"===== 相手を {feat} で5分位に切る =====")
        print(f"{'分位':>8}{'範囲':>18}{'窓':>5}{'行数':>9}{'平均順位':>9}"
              f"{'的中%':>8}{'ROI':>8}{'CI下限':>8}{'中央払戻':>10}")
        for i in range(5):
            lo = -9 if i == 0 else qs[i - 1]
            hi = 9 if i == 4 else qs[i]
            for wn, src in (("探索", seg_s), ("確認", seg_c)):
                sub = [x for x in src if lo <= x[feat] < hi]
                if len(sub) < 500:
                    continue
                r, l = roi_ci(sub)
                pl = sorted(x["pay"] for x in sub if x["pay"] > 0)
                mk = " 🟢" if l > PAYOUT_RATE else ""
                rng = f"{lo:.3f}〜{hi:.3f}" if wn == "探索" else ""
                print(f"{f'Q{i+1}' if wn=='探索' else '':>8}{rng:>18}{wn:>5}{len(sub):>9,}"
                      f"{np.mean([x['rank'] for x in sub]):>9.2f}"
                      f"{sum(1 for x in sub if x['pay']>0)/len(sub):>8.2%}"
                      f"{r:>8.1%}{l:>8.1%}{(np.median(pl) if pl else 0):>10,.0f}{mk}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
