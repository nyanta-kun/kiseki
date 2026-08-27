#!/usr/bin/env python3
"""9車の型F向け：**1着を軸2車に固定した三連単フォーメーション**を測る（2026-08-28）。

## なぜこの形か

`typef_anatomy.py` の分解で、型Fの律速は **① 軸2車がともに3着以内（31.35%）** と分かった。
相手を増やしても①は動かないので届かない（`typef_fix.py`・ROI 64〜70%）。
一方 **軸1だけなら3着以内 64.59%**。**軸2車のどちらかが1着**なら、
「両方が3着以内」よりずっと緩い条件になる。

    買い目 = 1着 ∈ {軸1, 軸2} × 2着 ∈ 指数上位K車 × 3着 ∈ 指数上位M車（重複は除く）

🔴 **点数は自動で決まる**（K・M と軸の位置から）。1点あたり = 10,000 ÷ 点数。
   点数が増えれば払戻は反比例で下がるので、**的中率と払戻の交換**にしかならない
   可能性がある。それを確かめるのがこのスクリプト。
"""
from __future__ import annotations

import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

BUDGET = 10_000
WALL = 74.85


def load(mode: str, d1: str, d2: str, label: str):
    q = ("SELECT race_key, race_date, axis1, axis2 FROM type_lab_picks "
         "WHERE mode = ? AND settled_at IS NOT NULL AND plan_key = 'F_hit' "
         "  AND type_label = ? AND race_date BETWEEN ? AND ?")
    with get_connection() as c:
        return [dict(zip(("race_key", "race_date", "axis1", "axis2"), tuple(r)))
                for r in c.execute(q, (mode, label, d1, d2)).fetchall()]


def order_of(keys):
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, pred_top3_pct FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))})")
            got = defaultdict(dict)
            for rk, fn, p in (tuple(r) for r in c.execute(q, ch).fetchall()):
                if p is not None:
                    got[str(rk)][int(fn)] = float(p)
            for rk, d in got.items():
                out[rk] = sorted(d, key=lambda c_: (-d[c_], c_))
    return out


def finishes(keys):
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND finish_order BETWEEN 1 AND 3")
            got = defaultdict(dict)
            for rk, fn, fo in (tuple(r) for r in c.execute(q, ch).fetchall()):
                got[str(rk)][int(fo)] = int(fn)
            for rk, d in got.items():
                if set(d) == {1, 2, 3}:
                    out[rk] = (d[1], d[2], d[3])
    return out


def odds_of(keys, combos):
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = [k for k in keys[i:i + 900] if k in combos]
            if not ch:
                continue
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND bet_type = 'trifecta'")
            board = {}
            for rk, comb, od in (tuple(r) for r in c.execute(q, ch).fetchall()):
                board[(str(rk), str(comb))] = float(od or 0)
            for rk in ch:
                v = board.get((rk, combos[rk]))
                if v:
                    out[rk] = v
    return out


def n_points(a1, a2, second, third) -> int:
    n = 0
    for x in (a1, a2):
        for y in second:
            if y == x:
                continue
            for z in third:
                if z in (x, y):
                    continue
                n += 1
    return n


def evaluate(rows, order, fin, K, M, title):
    combos, kept = {}, []
    for r in rows:
        rk = r["race_key"]
        if rk not in order or rk not in fin:
            continue
        o = order[rk]
        a1, a2 = int(r["axis1"]), int(r["axis2"])
        second, third = o[:K], o[:M]
        np_ = n_points(a1, a2, second, third)
        if np_ <= 0:
            continue
        stake = BUDGET // np_ // 100 * 100
        if stake <= 0:
            continue
        f = fin[rk]
        hit = (f[0] in (a1, a2) and f[1] in second and f[2] in third)
        kept.append((r, np_, stake, hit))
        if hit:
            combos[rk] = "-".join(str(x) for x in f)
    if not kept:
        return
    od = odds_of([r["race_key"] for r, *_ in kept], combos)
    inv = ret = nhit = nshown = nbig = 0
    pays, days, pts = [], set(), []
    for r, np_, stake, hit in kept:
        days.add(r["race_date"])
        inv += stake * np_
        pts.append(np_)
        if hit and r["race_key"] in od:
            pay = int(od[r["race_key"]] * stake)
            ret += pay
            nhit += 1
            pays.append(pay)
            if pay > stake * np_:
                nshown += 1
            if pay >= 100_000:
                nbig += 1
    n = len(kept)
    nd = max(len(days), 1)
    mark = "🟢" if inv and ret / inv * 100 > WALL else ""
    print(f"{title:20}{n:6d}{stx.median(pts):6.0f}"
          f"{BUDGET // int(stx.median(pts)) // 100 * 100:8,}"
          f"{nhit / n * 100:8.2f}%{nshown / n * 100:9.2f}%"
          f"{(stx.median(pays) if pays else 0):10,.0f}{nbig / nd:9.3f}"
          f"{(ret / inv * 100 if inv else 0):7.1f}%{mark}")


def main() -> None:
    for lab, w in (("確認窓 2026", ("2026-01-01", "2026-08-26")),
                   ("探索窓 2025", ("2025-01-01", "2025-12-31"))):
        rows = load("paper9", *w, "F")
        keys = [r["race_key"] for r in rows]
        order, fin = order_of(keys), finishes(keys)
        ok = [r for r in rows if r["race_key"] in order and r["race_key"] in fin]

        # まず素の確率を出す
        n = len(ok)
        w1 = sum(1 for r in ok
                 if fin[r["race_key"]][0] in (int(r["axis1"]), int(r["axis2"])))
        a1w = sum(1 for r in ok if fin[r["race_key"]][0] == int(r["axis1"]))
        a2w = sum(1 for r in ok if fin[r["race_key"]][0] == int(r["axis2"]))
        print(f"\n{'=' * 92}\n== 9車 型F {lab}  n={n}")
        print(f"  軸1が1着            {a1w / n * 100:6.2f}%")
        print(f"  軸2が1着            {a2w / n * 100:6.2f}%")
        print(f"  **軸1か軸2が1着**    {w1 / n * 100:6.2f}%   "
              f"（参考: 軸2車ともに3着以内は 31.35%）")

        print(f"\n{'1着=軸2車 × 2着K × 3着M':20}{'n':>6}{'点数':>6}{'1点':>8}"
              f"{'的中':>9}{'表示的中':>10}{'払戻中央':>10}{'10万+/日':>9}{'ROI':>8}")
        for K in (3, 4, 5):
            for M in (4, 5, 6, 7):
                if M < K:
                    continue
                evaluate(ok, order, fin, K, M, f"  2着{K}車 × 3着{M}車")
        print(f"  {'（参考）現行 F_hit':18}{'':6}{12:6d}{800:8,}"
              f"{16.08:8.2f}%{13.65:9.2f}%{23360:10,}{0.065:9.3f}{64.3:7.1f}%")


if __name__ == "__main__":
    main()
