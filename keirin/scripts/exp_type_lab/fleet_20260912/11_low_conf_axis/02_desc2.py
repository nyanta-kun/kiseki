#!/usr/bin/env python3
"""層 × 市場との食い違い で、どの車が1着になっているか（②に望みがあるか）。"""
from __future__ import annotations
import pickle, sys
from pathlib import Path
import numpy as np

rows = pickle.loads(Path("/tmp/11_layers.pkl").read_bytes())
thr = pickle.loads(Path("/tmp/11_thr.pkl").read_bytes())


def inlay(r, nm):
    if nm == "(全体)":
        return True
    q, side, t = thr[nm]
    return (r[q] <= t) if side == "lo" else (r[q] >= t)


LAYERS = ["(全体)", "axis_sum_lo25", "p3_gap12_lo25", "pw_gap12_lo25",
          "pw_max_lo25", "pw_ent_hi10"]

print("=== 軸1が市場最人気か（mkt1 = 予測オッズから作った1着シェア最大） ===")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win]
    print(f"\n[{win}]")
    print("  {:18s} {:>7s} {:>9s} {:>9s} {:>8s} {:>8s} {:>8s} {:>8s} {:>8s}".format(
        "層", "n", "軸1=市場%", "軸1=◎%", "軸1勝%", "軸2勝%", "mkt1勝%", "pw1勝%", "◎勝%"))
    for nm in LAYERS:
        s = [r for r in sub if inlay(r, nm)]
        n = len(s)
        ag = sum(1 for r in s if r["a1"] == r["mkt1"]) / n * 100
        ah = sum(1 for r in s if r["a1"] == r["hon"]) / n * 100
        w1 = sum(1 for r in s if r["w1"] == r["a1"]) / n * 100
        w2 = sum(1 for r in s if r["w1"] == r["a2"]) / n * 100
        wm = sum(1 for r in s if r["w1"] == r["mkt1"]) / n * 100
        wp = sum(1 for r in s if r["w1"] == r["pw1"]) / n * 100
        wh = sum(1 for r in s if r["hon"] and r["w1"] == r["hon"]) / n * 100
        print(f"  {nm:18s} {n:7d} {ag:9.1f} {ah:9.1f} {w1:8.2f} {w2:8.2f}"
              f" {wm:8.2f} {wp:8.2f} {wh:8.2f}")

print("\n=== 層 × 「軸1が市場最人気でない」 のときだけ ===")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win and r["a1"] != r["mkt1"]]
    print(f"\n[{win}] 食い違い n={len(sub):,} "
          f"({len(sub)/len([r for r in rows if r['win']==win])*100:.1f}%)")
    print("  {:18s} {:>7s} {:>8s} {:>8s} {:>8s} {:>8s} {:>9s}".format(
        "層", "n", "軸1勝%", "軸2勝%", "mkt1勝%", "pw1勝%", "軸1圏外%"))
    for nm in LAYERS:
        s = [r for r in sub if inlay(r, nm)]
        n = len(s)
        if n < 50:
            continue
        w1 = sum(1 for r in s if r["w1"] == r["a1"]) / n * 100
        w2 = sum(1 for r in s if r["w1"] == r["a2"]) / n * 100
        wm = sum(1 for r in s if r["w1"] == r["mkt1"]) / n * 100
        wp = sum(1 for r in s if r["w1"] == r["pw1"]) / n * 100
        out = sum(1 for r in s if r["a1"] not in (r["w1"], r["w2"], r["w3"])) / n * 100
        print(f"  {nm:18s} {n:7d} {w1:8.2f} {w2:8.2f} {wm:8.2f} {wp:8.2f} {out:9.2f}")

print("\n=== 軸1 と pw1 / ◎ / mkt1 が一致する割合（全体） ===")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win]
    n = len(sub)
    print(f"  [{win}] a1==pw1 {sum(1 for r in sub if r['a1']==r['pw1'])/n*100:5.1f}%  "
          f"a1==mkt1 {sum(1 for r in sub if r['a1']==r['mkt1'])/n*100:5.1f}%  "
          f"a1==◎ {sum(1 for r in sub if r['a1']==r['hon'])/n*100:5.1f}%  "
          f"pw1==mkt1 {sum(1 for r in sub if r['pw1']==r['mkt1'])/n*100:5.1f}%")
