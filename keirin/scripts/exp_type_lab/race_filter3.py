#!/usr/bin/env python3
"""「1日30R前後へ絞る」候補を確認窓で横並びに測る（2026-08-27）。

🔴 **無作為に半分にした対照を必ず置く。** 絞った案が対照と区別できないなら、
   その絞り込みは ROI を動かしていない（件数が減って CI が広がっただけ）。
"""
from __future__ import annotations

import random
import statistics as stx
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from race_filter import (  # noqa: E402
    CONFIRM, EXPLORE, WALL, boot_ci, load, per_day, roi, shown_hit, window,
)


def kpi(name: str, rows: list[dict], n_days: int) -> None:
    if not rows:
        print(f"{name:28} —")
        return
    hits = [r for r in rows if r["hit"]]
    two = [r for r in hits if r["payout"] >= 2 * r["budget"]]
    big = [r for r in hits if r["payout"] >= 100_000]
    med = stx.median([r["payout"] for r in rows if r["payout"] > r["budget"]] or [0])
    inv = sum(r["budget"] for r in rows) / n_days
    lo, hi = boot_ci(rows)
    print(f"{name:28}{per_day(rows):6.1f}{shown_hit(rows):8.2f}%{med:9,.0f}"
          f"{len(two) / n_days:7.2f}{len(big) / n_days:8.3f}{inv:11,.0f}"
          f"{roi(rows):8.1f}%  [{lo:.1f}, {hi:.1f}]"
          f"{'🟢' if lo > WALL else ''}")


def main():
    rows = load()
    ex, cf = window(rows, EXPLORE), window(rows, CONFIRM)
    nd = len({r["race_date"] for r in cf})
    print(f"確認窓 {CONFIRM[0]}〜{CONFIRM[1]}  {len(cf)}R / {nd}日\n")
    print(f"{'案':28}{'件/日':>6}{'表示的中':>9}{'払戻中央':>9}{'2倍+/日':>7}"
          f"{'10万+/日':>8}{'投資/日':>11}{'ROI':>9}  95%CI")

    kpi("① 絞らない（現状）", cf, nd)

    # 無作為に半分（対照）。seed を変えて振れ幅も見る
    for seed in (1, 2, 3):
        rnd = random.Random(seed)
        half = [r for r in cf if rnd.random() < 0.5]
        kpi(f"② 無作為に半分 seed={seed}", half, nd)

    # 型で絞る（探索窓の順で上位4型）
    kpi("③ 型 B+F+D+C", [r for r in cf if r["type_label"] in ("B", "F", "D", "C")], nd)
    kpi("④ 型 A+B+C+D（堅い側）",
        [r for r in cf if r["type_label"] in ("A", "B", "C", "D")], nd)
    kpi("⑤ 型 A+B+C（堅い3型）",
        [r for r in cf if r["type_label"] in ("A", "B", "C")], nd)
    kpi("⑥ 型 D+E+F（混戦3型）",
        [r for r in cf if r["type_label"] in ("D", "E", "F")], nd)

    # 軸の堅さ（axis_sum）上位半分 / 下位半分
    vals = sorted(float(r["axis_sum"]) for r in cf if r["axis_sum"] is not None)
    mid = vals[len(vals) // 2]
    kpi(f"⑦ axis_sum >= {mid:.2f}（堅い半分）",
        [r for r in cf if r["axis_sum"] is not None and float(r["axis_sum"]) >= mid], nd)
    kpi(f"⑧ axis_sum <  {mid:.2f}（混戦半分）",
        [r for r in cf if r["axis_sum"] is not None and float(r["axis_sum"]) < mid], nd)

    # 想定平均払戻の低い側（当たりやすい側）/ 高い側
    pv = sorted(float(r["pred_mean_payout"]) for r in cf if r["pred_mean_payout"])
    pmid = pv[len(pv) // 2]
    kpi(f"⑨ 想定払戻 <  {pmid:,.0f}円", [r for r in cf if r["pred_mean_payout"]
                                     and float(r["pred_mean_payout"]) < pmid], nd)
    kpi(f"⑩ 想定払戻 >= {pmid:,.0f}円", [r for r in cf if r["pred_mean_payout"]
                                     and float(r["pred_mean_payout"]) >= pmid], nd)

    # 種別（探索窓のROI上位で30件/日になる点）
    keep = ("チャレンジ決勝", "準決勝", "選抜", "チャレンジ予選",
            "チャレンジ準決勝", "特選", "予選")
    kpi("⑪ 種別7つ（探索窓で選抜）", [r for r in cf if r["race_type"] in keep], nd)

    print("\n-- 探索窓でも同じ表（案が窓をまたいで同じ向きか）")
    nde = len({r["race_date"] for r in ex})
    kpi("① 絞らない（現状）", ex, nde)
    kpi("③ 型 B+F+D+C", [r for r in ex if r["type_label"] in ("B", "F", "D", "C")], nde)
    kpi("⑤ 型 A+B+C（堅い3型）",
        [r for r in ex if r["type_label"] in ("A", "B", "C")], nde)
    kpi("⑥ 型 D+E+F（混戦3型）",
        [r for r in ex if r["type_label"] in ("D", "E", "F")], nde)
    exv = sorted(float(r["axis_sum"]) for r in ex if r["axis_sum"] is not None)
    exmid = exv[len(exv) // 2]
    kpi(f"⑦ axis_sum >= {exmid:.2f}（堅い半分）",
        [r for r in ex if r["axis_sum"] is not None and float(r["axis_sum"]) >= exmid], nde)
    kpi("⑪ 種別7つ（探索窓で選抜）", [r for r in ex if r["race_type"] in keep], nde)


if __name__ == "__main__":
    main()
