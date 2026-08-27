#!/usr/bin/env python3
"""EV / 的中確率 / 軸信頼 の向きが**プランごとに両窓で一致するか**（2026-08-27）。

プールした差は、効くプランと効かないプランの平均でしかない。
プラン内の順位付けとして使えるかは**プランごとに両窓で同符号か**で決まる。
"""
from __future__ import annotations

import statistics as stx
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ev_axis_rank import NQ, PLANS, edges_from, load, qof, win  # noqa: E402
from race_filter import CONFIRM, EXPLORE, boot_ci, per_day, roi, shown_hit  # noqa: E402


def main():
    rows = load()
    ex, cf = win(rows, EXPLORE), win(rows, CONFIRM)
    for key in ("ev", "psum", "axis_sum"):
        edges = {p: edges_from([r for r in ex if r["plan_key"] == p], key)
                 for p in PLANS}
        print(f"\n== {key}：上位1/5 − 下位1/5（ROI pt）")
        print(f"{'plan':8}{'探索':>9}{'確認':>9}   判定")
        agree = 0
        for p in PLANS:
            d = {}
            for lab, ws in (("ex", ex), ("cf", cf)):
                g = [r for r in ws if r["plan_key"] == p and r[key] is not None
                     and edges[p]]
                lo = [r for r in g if qof(float(r[key]), edges[p]) == 0]
                hi = [r for r in g if qof(float(r[key]), edges[p]) == NQ - 1]
                d[lab] = (roi(hi) - roi(lo)) if (lo and hi) else None
            if d["ex"] is None or d["cf"] is None:
                continue
            same = d["ex"] * d["cf"] > 0
            agree += 1 if same else 0
            mark = ("🟢 両窓プラス" if same and d["cf"] > 0
                    else "🔵 両窓マイナス" if same else "🔴 逆")
            print(f"{p:8}{d['ex']:+8.1f}{d['cf']:+9.1f}   {mark}")
        print(f"   → 両窓で同符号: {agree}/6")

    # 採用案の KPI（軸信頼の下位2/5をプラン内で外す）
    edges = {p: edges_from([r for r in ex if r["plan_key"] == p], "axis_sum")
             for p in PLANS}
    nd = len({r["race_date"] for r in cf})
    print(f"\n== 案: プラン内で軸信頼の下位2/5 を外す（確認窓 {nd}日）")
    print(f"{'':22}{'件/日':>7}{'表示的中':>9}{'払戻中央':>10}{'2倍+/日':>8}"
          f"{'10万+/日':>9}{'投資/日':>11}{'ROI':>8}")
    for lab, sel in (("絞らない", lambda r: True),
                     ("下位2/5を外す",
                      lambda r: r["axis_sum"] is not None
                      and edges[r["plan_key"]]
                      and qof(float(r["axis_sum"]), edges[r["plan_key"]]) >= 2)):
        g = [r for r in cf if sel(r)]
        hits = [r for r in g if r["hit"]]
        two = [r for r in hits if r["payout"] >= 2 * r["budget"]]
        big = [r for r in hits if r["payout"] >= 100_000]
        med = stx.median([r["payout"] for r in g if r["payout"] > r["budget"]] or [0])
        print(f"{lab:22}{per_day(g):7.1f}{shown_hit(g):8.2f}%{med:10,.0f}"
              f"{len(two) / nd:8.2f}{len(big) / nd:9.3f}"
              f"{sum(r['budget'] for r in g) / nd:11,.0f}{roi(g):7.1f}%")
        if lab != "絞らない":
            print(f"{'':22}CI{boot_ci(g)}")
        # プラン別の残り件数
        if lab != "絞らない":
            per = {}
            for r in g:
                per[r["plan_key"]] = per.get(r["plan_key"], 0) + 1
            print("   プラン別 件/日:",
                  "  ".join(f"{p}={per.get(p, 0) / nd:.1f}" for p in PLANS))


if __name__ == "__main__":
    main()
