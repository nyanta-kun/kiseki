#!/usr/bin/env python3
"""第5章 まだ測っていない切り口のセル別実測（2026-09-10）。

開催日目 / 種別 / バンク周長 / 屋内 / 会場 ごとに、**現行の商品**（日次上限まで
当てたあと）の表示的中・ROI を両窓で出し、**順位が窓をまたいで残るか**を見る。

🔴 `race_selection_2026_08_31.md` の警告どおり、ここで良く見えるセルを
   そのまま腕にしてはいけない。腕は `split_arms.py` で無作為対照20本と比べる。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.split_common import apply_cap, load_races, window  # noqa: E402

BANK = {}
for ln in """11 333 0/12 333 0/13 333 0/14 333 0/15 333 0/16 333 0/17 333 0/21 400 0/
22 333 0/23 400 0/24 333 0/25 400 0/26 400 0/27 400 0/28 500 0/31 333 0/32 250 1/
34 333 0/35 500 0/36 333 0/37 333 0/38 500 0/41 400 0/42 400 0/43 400 0/44 400 0/
45 400 0/46 400 0/47 333 0/48 400 0/51 400 0/52 333 0/53 400 0/54 333 0/55 400 0/
56 333 0/57 333 0/61 400 0/62 333 0/63 333 0/64 333 0/71 333 0/72 333 0/73 333 0/
74 333 0/75 333 0/81 400 0/82 400 0/83 333 0/84 400 0/85 333 0/86 400 0/87 400 0/
88 333 0/89 400 0""".replace("\n", "").split("/"):
    c, b, i = ln.split()
    BANK[c] = (int(b), int(i))


def cells(sold, keyf):
    g = defaultdict(lambda: [0, 0, 0.0, 0.0, 0])
    for r in sold:
        if not r["settled"]:
            continue
        a = g[keyf(r)]
        a[0] += 1
        a[1] += 1 if r["pay"] >= r["inv"] else 0
        a[2] += r["inv"]; a[3] += r["pay"]
        a[4] += 1 if r["pay"] >= 100_000 else 0
    return {k: dict(n=v[0], shown=v[1] / v[0] * 100, roi=v[3] / v[2] * 100,
                    big=v[4] / v[0] * 100) for k, v in g.items() if v[0] >= 60}


def show(title, W, keyf, order=None):
    A, B = cells(W[0], keyf), cells(W[1], keyf)
    ks = order or sorted(set(A) & set(B), key=lambda k: -B[k]["shown"])
    ks = [k for k in ks if k in A and k in B]
    print(f"\n■ {title}")
    print(f"    {'セル':22s} {'探索 n':>7s} {'表示%':>7s} {'ROI%':>7s} | "
          f"{'確認 n':>7s} {'表示%':>7s} {'ROI%':>7s}")
    for k in ks:
        print(f"    {str(k):22s} {A[k]['n']:7d} {A[k]['shown']:6.2f}% {A[k]['roi']:6.1f}% | "
              f"{B[k]['n']:7d} {B[k]['shown']:6.2f}% {B[k]['roi']:6.1f}%")
    if len(ks) >= 4:
        ra = np.argsort(np.argsort([-A[k]["shown"] for k in ks]))
        rb = np.argsort(np.argsort([-B[k]["shown"] for k in ks]))
        rs = np.corrcoef(ra, rb)[0, 1]
        ra2 = np.argsort(np.argsort([-A[k]["roi"] for k in ks]))
        rb2 = np.argsort(np.argsort([-B[k]["roi"] for k in ks]))
        print(f"    → 窓をまたぐ順位相関 Spearman: 表示的中 {rs:+.2f} / "
              f"ROI {np.corrcoef(ra2, rb2)[0,1]:+.2f}  （セル {len(ks)}）")


def main():
    R = load_races()
    W = [apply_cap(window(R, w)) for w in ("explore", "confirm")]
    vc = lambda r: r["race_key"].split("_")[1]
    show("開催日目 day_index", W, lambda r: f"{r['dayidx']}日目",
         order=[f"{i}日目" for i in range(1, 8)])
    show("開催日目 × 決勝系か", W,
         lambda r: f"{min(r['dayidx'],5)}日目/{'決勝系' if '決勝' in r['rtype'] else '一般'}")
    show("種別", W, lambda r: r["rtype"])
    show("バンク周長", W, lambda r: f"{BANK.get(vc(r), (0,0))[0]}m",
         order=["250m", "333m", "400m", "500m"])
    show("屋内(千葉)", W, lambda r: "屋内" if BANK.get(vc(r), (0, 0))[1] else "屋外")
    show("会場", W, lambda r: r["venue"])
    show("プラン", W, lambda r: r["plan"])
    show("1日に同じ会場から何件目か", W,
         lambda r: f"{min(r['_seq'], 6)}件目" if "_seq" in r else "?")


def annotate_seq(sold):
    by = defaultdict(list)
    for r in sold:
        by[(r["date"], r["venue"])].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["race_no"])
        for i, r in enumerate(v, 1):
            r["_seq"] = i


if __name__ == "__main__":
    R = load_races()
    W = [apply_cap(window(R, w)) for w in ("explore", "confirm")]
    for s in W:
        annotate_seq(s)
    vc = lambda r: r["race_key"].split("_")[1]
    show("開催日目 day_index", W, lambda r: f"{r['dayidx']}日目",
         order=[f"{i}日目" for i in range(1, 8)])
    show("開催日目 × 決勝系か", W,
         lambda r: f"{min(r['dayidx'],5)}日目/{'決勝系' if '決勝' in r['rtype'] else '一般'}")
    show("種別", W, lambda r: r["rtype"])
    show("バンク周長", W, lambda r: f"{BANK.get(vc(r), (0,0))[0]}m",
         order=["250m", "333m", "400m", "500m"])
    show("屋内(千葉)", W, lambda r: "屋内" if BANK.get(vc(r), (0, 0))[1] else "屋外")
    show("1日に同じ会場から何件目か", W, lambda r: f"{min(r['_seq'],6)}件目",
         order=[f"{i}件目" for i in range(1, 7)])
    show("会場", W, lambda r: r["venue"])
