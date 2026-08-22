#!/usr/bin/env python3
"""軸2の信頼度で**買い目の形を切り替える**（ユーザー方針 2026-08-23）。

## 前提（測定済み）

- 軸2の「選び方」を変えても動かない（`exp_trio_axis2.py`・逆向きが確認窓で最高ROI）
- **だが「飛ぶ条件」は正確に予測できる**（`exp_trio_axis2_conditions.py`・
  二軸的中率が 36%〜75.5% と2倍以上動き、探索窓と確認窓でほぼ完全に一致）
- レース形状シグナルは ROI には残らない（市場が織り込む・`exp_trio_shape_priced.py`）

🔴 **したがって狙いは妙味ではなく「形を状況に合わせる」こと。**
   二軸的中が36%しかないレースで軸2車固定の三連複を売るのは構造的に無理がある。

## 信頼度スコア

    conf = z(axis_sum) − z(bad2)        （レース間で標準化）

`axis_sum` = p3上位2車の合計（二軸的中の最大の駆動因・36.2%↔75.5%）
`bad2` = 軸2の大敗率（**市場直交ヘッド**・単独で20ptの分離）
🔴 閾値は**探索窓の分位でだけ**決め、確認窓は一度きり見る。

## 比べる形（すべて1レース1万円・均等）

| 記号 | 買い目 | ねらい |
|---|---|---|
| `A` 二軸1点 | {軸1, 軸2, 順位5} | 現行の最良（ROI 83.5%） |
| `B` 軸2ヘッジ2点 | A + {軸1, 順位3, 順位5} | **軸2が飛んでも順位3で拾う** |
| `C` 軸1流し3点 | A + {軸1,順位3,順位5} + {軸1,順位4,順位5} | 軸2を要求しない |
| `D` 二軸2点 | {軸1,軸2,順位5} + {軸1,軸2,順位3} | 相手を増やす（比較用） |

⚠️ **点数を変えると1点あたりの賭け金が変わる**（予算固定）。的中率と払戻額は
   必ず逆方向に動くので ROI・100%超の日・0円の日を同時に見ること。
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    ap.add_argument("--cap", type=int, default=20)
    args = ap.parse_args()
    np.random.seed(191)

    rows = []
    with open(args.cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win") and r.get("bad"):
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

    recs = []
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        p3 = {int(k): v for k, v in r["p3"].items()}
        bad = {int(k): v for k, v in r["bad"].items()}
        if len(p3) < 7 or len(bad) < 7:
            continue
        o = [c for c, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
        a1, a2 = o[0], o[1]
        top3 = {int(x) for w in r["win"] for x in w.split("-")}
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        shapes = {
            "A 二軸1点": [frozenset((a1, a2, o[4]))],
            "B 軸2ヘッジ2点": [frozenset((a1, a2, o[4])), frozenset((a1, o[2], o[4]))],
            "C 軸1流し3点": [frozenset((a1, a2, o[4])), frozenset((a1, o[2], o[4])),
                          frozenset((a1, o[3], o[4]))],
            "D 二軸2点": [frozenset((a1, a2, o[4])), frozenset((a1, a2, o[2]))],
        }
        res = {}
        for name, legs in shapes.items():
            legs = [k for k in legs if k in bd]
            if not legs:
                continue
            stake = unit_stake(len(legs))
            pay = next((int(bd[k] * 100) * stake // 100 for k in legs if k in wins), 0)
            res[name] = (stake * len(legs), pay, len(legs))
        if not res:
            continue
        recs.append(dict(date=r["race_date"], axis_sum=p3[a1] + p3[a2], bad2=bad[a2],
                         both=int(a1 in top3 and a2 in top3), res=res))

    sel = [x for x in recs if x["date"] < args.split]
    conf = [x for x in recs if x["date"] >= args.split]
    # 🔴 標準化と閾値は探索窓でだけ決める
    ma, sa = st.mean([x["axis_sum"] for x in sel]), st.pstdev([x["axis_sum"] for x in sel])
    mb, sb = st.mean([x["bad2"] for x in sel]), st.pstdev([x["bad2"] for x in sel])
    for x in recs:
        x["conf"] = (x["axis_sum"] - ma) / (sa or 1) - (x["bad2"] - mb) / (sb or 1)
    q = np.quantile([x["conf"] for x in sel], [1 / 3, 2 / 3])
    print(f"{len(recs):,}R  信頼度 conf = z(axis_sum) − z(bad2)  閾値 {q[0]:.2f}/{q[1]:.2f}")
    grp = lambda x: "低信頼" if x["conf"] < q[0] else ("高信頼" if x["conf"] >= q[1] else "中")
    for g in ("低信頼", "中", "高信頼"):
        a = [x for x in sel if grp(x) == g]; b = [x for x in conf if grp(x) == g]
        print(f"  {g}: 二軸的中 探索 {np.mean([x['both'] for x in a]):.1%} / "
              f"確認 {np.mean([x['both'] for x in b]):.1%}")
    print()

    def day_kpi(seg, name, cap):
        by = defaultdict(list)
        for x in seg:
            if name in x["res"]:
                by[x["date"]].append(x["res"][name])
        days = []
        for d, v in by.items():
            v = v[:cap]
            days.append((sum(z[0] for z in v), sum(z[1] for z in v), len(v),
                         sum(1 for z in v if z[1] > 0), [z[1] for z in v if z[1] > 0]))
        if not days:
            return None
        n = len(days); bet = sum(x[0] for x in days); pay = sum(x[1] for x in days)
        rois = [x[1] / x[0] for x in days]
        pl = sorted(p for x in days for p in x[4])
        return dict(roi=pay / bet, hit=sum(x[3] for x in days) / sum(x[2] for x in days),
                    over=sum(1 for r in rois if r >= 1) / n,
                    zero=sum(1 for r in rois if r == 0) / n,
                    med=(st.median(pl) if pl else 0), n=sum(x[2] for x in days))

    print(f"{'群':8}{'形':16}{'窓':>5}{'R数':>8}{'的中%':>8}{'ROI':>8}"
          f"{'100%超':>8}{'0円日':>7}{'中央払戻':>10}")
    for g in ("高信頼", "中", "低信頼"):
        for name in ("A 二軸1点", "B 軸2ヘッジ2点", "C 軸1流し3点", "D 二軸2点"):
            for wn, src in (("探索", sel), ("確認", conf)):
                k = day_kpi([x for x in src if grp(x) == g], name, args.cap)
                if not k:
                    continue
                print(f"{g if (name.startswith('A') and wn=='探索') else '':8}"
                      f"{name if wn=='探索' else '':16}{wn:>5}{k['n']:>8,}"
                      f"{k['hit']:>8.2%}{k['roi']:>8.1%}{k['over']:>8.1%}"
                      f"{k['zero']:>7.1%}{k['med']:>10,.0f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
