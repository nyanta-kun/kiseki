#!/usr/bin/env python3
"""絞り込み案の追試（2026-08-27）。

race_filter.py の続き。**探索窓で決めた順位が確認窓で再現するか**だけを見る。
再現しない量で絞っても ROI は上がらない（選択効果を自分で作るだけ）。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from race_filter import (  # noqa: E402
    CONFIRM, EXPLORE, WALL, boot_ci, load, per_day, roi, shown_hit, window,
)


def spearman(a: list[float], b: list[float]) -> float:
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def group(rows, key):
    d = defaultdict(list)
    for r in rows:
        d[str(r[key])].append(r)
    return d


def reproducibility(ex, cf, key, min_n=60):
    ge, gc = group(ex, key), group(cf, key)
    keys = [k for k in ge if len(ge[k]) >= min_n and len(gc.get(k, [])) >= min_n]
    if len(keys) < 4:
        return None
    a = [roi(ge[k]) for k in keys]
    b = [roi(gc[k]) for k in keys]
    return spearman(a, b), len(keys), keys, a, b


def main():
    rows = load()
    ex, cf = window(rows, EXPLORE), window(rows, CONFIRM)

    print("== 型（type_label）別")
    for label, g in (("探索窓", ex), ("確認窓", cf)):
        gg = group(g, "type_label")
        print(f"-- {label}")
        for k in sorted(gg, key=lambda x: -roi(gg[x])):
            v = gg[k]
            print(f"   {k}  {len(v):5d}  {per_day(v):5.2f}件/日  "
                  f"表示的中 {shown_hit(v):5.2f}%  ROI {roi(v):6.1f}%"
                  f"{'🟢' if roi(v) > WALL else '🔴'}")

    print("\n== 探索窓の型の順で積み上げ → 確認窓で評価")
    ge = group(ex, "type_label")
    order = sorted(ge, key=lambda x: -roi(ge[x]))
    keep = []
    for k in order:
        keep.append(k)
        sub = [r for r in cf if r["type_label"] in keep]
        print(f"   {len(keep)}型 {'+'.join(keep):20} {per_day(sub):6.2f}件/日 "
              f"表示的中 {shown_hit(sub):5.2f}%  ROI {roi(sub):6.1f}% CI{boot_ci(sub)}")

    print("\n== 窓をまたいだ順位の再現（Spearman・+1 なら完全再現 / 0 ならノイズ）")
    for key, min_n in (("race_type", 60), ("type_label", 60), ("venue_name", 120)):
        got = reproducibility(ex, cf, key, min_n)
        if not got:
            print(f"   {key:12} 群が足りない")
            continue
        rho, n, keys, a, b = got
        print(f"   {key:12} rho={rho:+.3f}  ({n}群)")
        if key == "race_type":
            worst = sorted(zip(keys, a, b), key=lambda t: t[2] - t[1])[:3]
            best = sorted(zip(keys, a, b), key=lambda t: t[1] - t[2])[:3]
            for lab, items in (("落ちた", worst), ("上がった", best)):
                for k, x, y in items:
                    print(f"      {lab:5} {k:14} 探索 {x:6.1f}% → 確認 {y:6.1f}%")

    print("\n== 参考: 全体（絞らない）")
    for label, g in (("探索窓", ex), ("確認窓", cf)):
        print(f"   {label} {per_day(g):.2f}件/日 ROI {roi(g):.1f}% CI{boot_ci(g)}")


if __name__ == "__main__":
    main()
