#!/usr/bin/env python3
"""短期量による『軸崩壊しそうなレースを売らない』の商品KPI（2026-09-14）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/shortform_filter.py [frac]

比較相手は **現行（全件）**。同数を落とす無作為対照 20seed と、
既に本番が持っているレバー（axis_sum だけで同数落とす）も並べる。
"""
from __future__ import annotations

import random
import sys
from statistics import median

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from shortform import F, _cands, load, wins  # noqa: E402


def gap(r, nm, k=3):
    v = F(r, nm)
    cand = _cands(r)[:k]
    return (v[r["a1"] - 1] + v[r["a2"] - 1]) / 2 - float(np.mean([v[c - 1] for c in cand]))


def kpi(rows):
    n = len(rows)
    if n == 0:
        return dict(n=0)
    inv = sum(r["inv"] for r in rows)
    pay = sum(r["pay"] for r in rows)
    shown = sum(1 for r in rows if r["pay"] >= r["inv"])
    days = len({r["date"] for r in rows})
    pays = sorted(r["pay"] for r in rows if r["pay"] > 0)
    return dict(n=n, perday=n / days, shown=shown / n * 100, roi=pay / inv * 100,
                med=median(pays) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / days,
                bust=np.mean([not r["both_in3"] for r in rows]) * 100)


def show(name, s, base=None):
    if not s["n"]:
        print(f"  {name:34s} (該当なし)")
        return
    d = f"{s['shown']-base['shown']:+7.2f}pt" if base else " " * 9
    print(f"  {name:34s}{s['n']:7d}{s['perday']:8.2f}{s['shown']:9.2f}%{s['roi']:8.1f}%"
          f"{s['med']:9,.0f}{s['big']:8.3f}{s['bust']:8.2f}%{d}")


def main():
    frac = float(sys.argv[1]) if len(sys.argv) > 1 else 0.20
    W = wins(load())
    ARMS = {
        "短期: 直近10走 正規化着順 gap": lambda r: gap(r, "ord10"),
        "短期: 直近5走 正規化着順 gap": lambda r: gap(r, "ord5"),
        "短期: 直近10走 3着内率 gap(符号反転)": lambda r: -gap(r, "top310"),
        "短期: 着順gap + 3着内率gap": lambda r: gap(r, "ord10") * 2 - gap(r, "top310"),
        "既存: axis_sum（低い側を落とす）": lambda r: -r["axis"],
        "既存: rp_sd（高い側を落とす）": lambda r: r["rp_sd"],
        "既存+短期: axis_sum z + 着順gap z": None,
    }
    print(f"落とす割合 {frac:.0%}（スコアの高い側＝軸崩壊しそうな側を落とす）\n")
    print(f"  {'腕':34s}{'件数':>7}{'件/日':>8}{'表示的中':>10}{'ROI':>9}"
          f"{'払戻中央':>9}{'10万+/日':>8}{'軸崩壊':>9}{'Δ表示的中':>9}")
    for w in ("confirm", "explore"):
        rows = W[w]
        base = kpi(rows)
        print(f"\n--- {w} ---")
        show("現行（全件）", base)
        ndrop = int(len(rows) * frac)
        for name, fn in ARMS.items():
            if fn is None:
                az = np.array([r["axis"] for r in rows])
                gz = np.array([gap(r, "ord10") for r in rows])
                az = (az - np.nanmean(az)) / np.nanstd(az)
                gz = (gz - np.nanmean(gz)) / np.nanstd(gz)
                sc = -az + gz
            else:
                sc = np.array([fn(r) for r in rows], float)
            sc = np.where(np.isfinite(sc), sc, -np.inf)
            keep = np.argsort(-sc)[ndrop:]
            show(name, kpi([rows[i] for i in keep]), base)
        ctrl = []
        for s in range(20):
            rng = random.Random(2000 + s)
            idx = list(range(len(rows)))
            rng.shuffle(idx)
            ctrl.append(kpi([rows[i] for i in idx[ndrop:]])["shown"])
        print(f"  {'無作為対照 20seed':34s}{'':7s}{'':8s}"
              f"{np.mean(ctrl):9.2f}%  (min {min(ctrl):.2f} / max {max(ctrl):.2f})")


if __name__ == "__main__":
    main()
