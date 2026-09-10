#!/usr/bin/env python3
"""第5章b 「1日に同じ会場から何件目か」は種別・プランの言い換えでないか（2026-09-10）。"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.split_common import apply_cap, load_races, window  # noqa: E402


def annotate(sold):
    by = defaultdict(list)
    for r in sold:
        by[(r["date"], r["venue"])].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["race_no"])
        for i, r in enumerate(v, 1):
            r["_seq"] = i
            r["_nven"] = len(v)


def tab(rows, kf, sf, minn=40):
    g = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        if not r["settled"]:
            continue
        a = g[kf(r)][sf(r)]
        a[0] += 1
        a[1] += 1 if r["pay"] >= r["inv"] else 0
    return g, minn


def show(title, W, kf, sf, sub_order):
    print(f"\n■ {title}")
    for lab, rows in zip(("探索", "確認"), W):
        g, minn = tab(rows, kf, sf)
        ks = sorted(g)
        print(f"  {lab}: " + " | ".join(f"{s}" for s in sub_order))
        for k in ks:
            cells = []
            for s in sub_order:
                a = g[k].get(s)
                cells.append(f"{a[1]/a[0]*100:5.1f}%(n={a[0]:4d})"
                             if a and a[0] >= minn else f"{'—':>12s}")
            print(f"    {str(k):12s} " + " ".join(cells))


def main():
    R = load_races()
    W = [apply_cap(window(R, w)) for w in ("explore", "confirm")]
    for s in W:
        annotate(s)
    seq = lambda r: f"{min(r['_seq'],6)}件目"
    order = [f"{i}件目" for i in range(1, 7)]
    show("会場内の順番 × 決勝系か", W,
         lambda r: "決勝系" if "決勝" in r["rtype"] else "一般",  seq, order)
    show("会場内の順番 × プラン", W, lambda r: r["plan"], seq, order)
    show("会場内の順番 × その会場のその日の件数", W,
         lambda r: f"会場{min(r['_nven'],8)}件日", seq, order)
    # 交絡の正体: 遅い番号ほど何が増えるか
    print("\n■ 会場内の順番ごとの中身（確認窓）")
    rows = [r for r in W[1] if r["settled"]]
    for i in range(1, 7):
        g = [r for r in rows if min(r["_seq"], 6) == i]
        if not g:
            continue
        fin = sum(1 for r in g if "決勝" in r["rtype"]) / len(g) * 100
        rno = np.mean([r["race_no"] for r in g])
        pl = defaultdict(int)
        for r in g:
            pl[r["plan"]] += 1
        top = sorted(pl.items(), key=lambda x: -x[1])[:3]
        print(f"    {i}件目 n={len(g):5d}  決勝系 {fin:5.1f}%  平均R番 {rno:4.1f}  "
              f"主なプラン " + " ".join(f"{k}{v/len(g)*100:.0f}%" for k, v in top))


if __name__ == "__main__":
    main()
