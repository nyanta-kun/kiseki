#!/usr/bin/env python3
"""外れの分解 — 「レース選定 / 相手選定 / 順番選定」のどれが効くのかを条件別に見る。

分解（排他・この順に判定）:
  ① 的中          買い目に決着が入っていた
  ② 順序違い      集合（3車）は買っていたが並びが違う          → **順番**の問題
  ③ 相手外し      軸2車は3着以内なのに集合を買えていない       → **相手**の問題
  ④ 軸崩壊        軸2車のどちらかが3着外                      → **レース選定/軸**の問題

🔴 まとめて見ると平均に埋もれるので、**ex-ante に分かる条件でだけ**割る
   （型・軸信頼・相手の開き・1着の読めなさ・実力伯仲・種別・開催日目・印一致）。
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict

import numpy as np

ROWS = pickle.load(open("/tmp/miss_anatomy_rows.pkl", "rb"))
W = {"explore": [r for r in ROWS if r["win"] == "explore"],
     "confirm": [r for r in ROWS if r["win"] == "confirm"]}


def modes(rows):
    n = len(rows)
    if not n:
        return None
    hit = sum(r["in_legs"] for r in rows)
    ordr = sum((not r["in_legs"]) and r["set_hit"] for r in rows)
    part = sum((not r["in_legs"]) and (not r["set_hit"]) and r["both_in3"] for r in rows)
    axis = n - hit - ordr - part
    inv = sum(r["inv"] for r in rows); pay = sum(r["pay"] for r in rows)
    shown = sum(r["shown"] for r in rows)
    return dict(n=n, hit=hit / n * 100, ordr=ordr / n * 100, part=part / n * 100,
                axis=axis / n * 100, shown=shown / n * 100, roi=pay / inv * 100)


def qcut(rows, field, q=5):
    v = np.array([r[field] for r in rows])
    edges = [np.percentile(v, 100 * j / q) for j in range(1, q)]
    return np.digitize(v, edges)


HEAD = ("    {:26s} {:>6s} {:>8s} {:>9s} {:>9s} {:>9s} {:>9s} {:>7s}"
        .format("条件", "n", "的中%", "順序違い%", "相手外し%", "軸崩壊%", "表示的中%", "ROI%"))


def show(label, rows, groups):
    print(f"\n  ── {label} " + "─" * 60)
    print(HEAD)
    for name, sub in groups:
        m = modes(sub)
        if not m or m["n"] < 80:
            continue
        print(f"    {name:26s} {m['n']:6,} {m['hit']:8.2f} {m['ordr']:9.2f} "
              f"{m['part']:9.2f} {m['axis']:9.2f} {m['shown']:9.2f} {m['roi']:7.1f}")


def main() -> None:
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"), ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        m = modes(rows)
        print("\n" + "=" * 108)
        print(f"=== {lab}   {len(rows):,}商品 / {nd}日 = {len(rows)/nd:.2f}件/日   "
              f"表示的中 {m['shown']:.2f}%  ROI {m['roi']:.1f}%")
        print("=" * 108)
        print(f"  全体の分解: 的中 {m['hit']:.2f}%  順序違い {m['ordr']:.2f}%  "
              f"相手外し {m['part']:.2f}%  軸崩壊 {m['axis']:.2f}%")

        show("型", rows, [(t, [r for r in rows if r["type"] == t]) for t in "ABCDEF"])
        show("プラン", rows, [(p, [r for r in rows if r["plan"] == p])
                            for p in sorted({r["plan"] for r in rows})])
        for f, nm in (("axis", "軸信頼 axis_sum"), ("gap", "相手の開き gap"),
                      ("pw_ent", "1着の読めなさ pw_ent"), ("rp_sd", "実力伯仲 rp_sd"),
                      ("sp5", "確率上位5点の厚み Σp5")):
            d = qcut(rows, f)
            show(f"{nm}（五分位）", rows,
                 [(f"Q{j+1}", [r for r, k in zip(rows, d) if k == j]) for j in range(5)])
        show("印一致 AGREE", rows, [("一致", [r for r in rows if r["agree"]]),
                                  ("不一致", [r for r in rows if not r["agree"]])])
        rtg = defaultdict(list)
        for r in rows:
            rtg[r["rtype"]].append(r)
        show("種別（多い順）", rows,
             sorted(((k, v) for k, v in rtg.items()), key=lambda kv: -len(kv[1]))[:10])
        show("開催日目", rows, [(f"{d}日目", [r for r in rows if r["dayi"] == d])
                             for d in sorted({r["dayi"] for r in rows})])


if __name__ == "__main__":
    main()
