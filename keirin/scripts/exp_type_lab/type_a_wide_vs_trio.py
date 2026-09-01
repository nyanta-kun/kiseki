#!/usr/bin/env python3
"""提案B の詰め: 三連複 小点数 か ワイド二軸1点 か（2026-08-31・ユーザー指示）。

## 前提の確認（実測済み）

- `wt_odds` は**確定払戻**（三連複で `wt_race_payouts/100` と 3,000件 100% 一致）。
  ワイドは `bet_type='quinellaPlace'`・組は `"1=4"` 形式。
- ワイドの**予測**オッズは板から導く: 2車 a,b を含む三連単30順列について
  `1/Σ(1/予測オッズ)`。三連複と同じ恒等式（PO_perm = 払戻率/p_perm）。

🔴 入稿ゲートは本番と同じ2つを腕ごとに掛け直す。1点のときゲートは
   「予測オッズ > 2.0倍」に一致する（平均想定払戻 = 10,000 × オッズ）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_wide_vs_trio.py
"""
from __future__ import annotations

import itertools
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                   # noqa: E402
import type_a_upset2 as M                            # noqa: E402
from src.database import get_connection              # noqa: E402


def load_wide(keys):
    out = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 400):
            ch = keys[i:i + 400]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type = 'quinellaPlace' AND race_key IN ({ph})",
                               tuple(ch)):
                d = dict(r)
                out[d["race_key"]][frozenset(int(x) for x in re.findall(r"\d+", d["combination"]))] \
                    = float(d["odds_value"])
    return out


def wide_po(d, pair):
    """ワイドの予測オッズ = 1/Σ(1/予測三連単オッズ)（その2車を含む30順列）。"""
    a, b = sorted(pair)
    inv = 0.0
    for t in M.CANON:
        if a in t and b in t:
            po = float(d["PO"][M.CIDX[t]])
            if po > 0:
                inv += 1.0 / po
    return 1.0 / inv if inv > 0 else 0.0


def play_wide(d, pairs):
    """ワイドを買う（ダッチ・本番ゲート）。"""
    po = [wide_po(d, p) for p in pairs]
    if any(x <= 0 for x in po) or len(pairs) * M.UNIT > M.BUDGET:
        return None
    w = [1.0 / x for x in po]
    n_units = M.BUDGET // M.UNIT
    units = [1] * len(pairs)
    rest = n_units - len(pairs)
    tot = sum(w)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / max(w[k], 1e-12))
        units[j] += 1
    stakes = [u * M.UNIT for u in units]
    if min(po) < M.MIN_POINT_ODDS:
        return None
    if sum(s * o for s, o in zip(stakes, po)) / len(pairs) <= M.MIN_MEAN_PAYOUT:
        return None
    top3 = set(d["f"])
    inv = sum(stakes)
    pay = 0.0
    for s, p in zip(stakes, pairs):
        if set(p) <= top3:
            fo = d["wide_final"].get(frozenset(p))
            if fo is None:
                return None
            pay += s * fo
    return dict(date=d["date"], inv=inv, pay=pay, k=len(pairs))


def main() -> int:
    data = M.load()
    wide = load_wide(sorted({d["key"] for d in data}))
    data = [d for d in data if wide.get(d["key"])]
    for d in data:
        d["wide_final"] = wide[d["key"]]
    print(f"台 {len(data):,}R（ワイドのある型A レース）")

    def mk_trio(n):
        return lambda d: ("trio", [frozenset((d["o"][0], d["o"][1], d["o"][k]))
                                   for k in range(2, 2 + n)])
    for n in (1, 2, 3):
        M.ARMS[f"三連複{n}点"] = mk_trio(n)

    WIDE = {
        "ワイド 軸2車1点": lambda d: [(d["o"][0], d["o"][1])],
        "ワイド 軸1流し2点": lambda d: [(d["o"][0], d["o"][1]), (d["o"][0], d["o"][2])],
        "ワイド 上位3車ボックス3点": lambda d: [(d["o"][0], d["o"][1]), (d["o"][0], d["o"][2]),
                                        (d["o"][1], d["o"][2])],
    }

    def run(rs, name, nd):
        if name in WIDE:
            return [r for r in (play_wide(d, WIDE[name](d)) for d in rs) if r]
        return [r for r in (M.play(d, name) for d in rs) if r]

    NAMES = ["A_hit 現行3点", "三連複1点", "三連複2点", "三連複3点",
             "ワイド 軸2車1点", "ワイド 軸1流し2点", "ワイド 上位3車ボックス3点"]

    for win, (lo, hi) in M.WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        nd = len({d["date"] for d in rs})
        print(f"\n{'='*120}\n=== {win}  型A {len(rs):,}R / {nd}日 ===")
        both = sum(1 for d in rs if d["o"][0] in d["f"] and d["o"][1] in d["f"])
        pw = sorted(wide_po(d, (d["o"][0], d["o"][1])) for d in rs)
        print(f"  参考: 軸2車がそろう率 {both/len(rs):.1%} / "
              f"ワイド軸2車の予測オッズ 中央 {median(pw):.2f}倍 / 2.0倍以上 "
              f"{sum(1 for x in pw if x >= 2.0)/len(pw):.1%}")
        print(M.HDR)
        for name in NAMES:
            print(M.row(name, M.summ(run(rs, name, nd), nd)))

        print(f"\n  ── ハイブリッド（通れば置換・通らなければ A_hit）──")
        print(M.HDR + "  置換%")
        for name in ("三連複2点", "ワイド 軸2車1点", "ワイド 軸1流し2点"):
            recs = []
            for d in rs:
                r = run([d], name, nd)
                r = r[0] if r else M.play(d, "A_hit 現行3点")
                if r:
                    recs.append(dict(r, alt=bool(run([d], name, nd))))
            s = M.summ(recs, nd)
            share = sum(1 for r in recs if r["alt"]) / len(recs) * 100
            print(M.row(f"ハイブリッド({name})", s) + f"{share:>8.1f}")

        print(f"\n  ── 同じレースでの直接対決（現行 → ハイブリッド・対応のあるブートストラップ）──")
        for name in ("三連複2点", "ワイド 軸2車1点", "ワイド 軸1流し2点"):
            pair = []
            for d in rs:
                a = M.play(d, "A_hit 現行3点")
                r = run([d], name, nd)
                b = r[0] if r else a
                if a and b:
                    pair.append((a, b))
            if not pair:
                continue
            rnd = random.Random(0)
            m = len(pair)
            dr, ds = [], []
            for _ in range(1500):
                ia = ib = pa = pb = 0.0
                sa = sb = 0
                for _ in range(m):
                    j = rnd.randrange(m)
                    x, y = pair[j]
                    ia += x["inv"]; pa += x["pay"]; sa += x["pay"] > x["inv"]
                    ib += y["inv"]; pb += y["pay"]; sb += y["pay"] > y["inv"]
                dr.append(pb / ib * 100 - pa / ia * 100)
                ds.append((sb - sa) / m * 100)
            dr.sort(); ds.sort()
            print(f"    ハイブリッド({name:<14}) n={m:>5,}  "
                  f"表示的中の差 CI[{ds[37]:+.2f},{ds[1462]:+.2f}]pt   "
                  f"ROIの差 CI[{dr[37]:+.1f},{dr[1462]:+.1f}]pt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
