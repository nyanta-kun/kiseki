#!/usr/bin/env python3
"""prod.py の腕を比べる（KPI + paired bootstrap + 維持/破壊/救済 + 到達可能性）。"""
from __future__ import annotations

import pickle
import sys
from statistics import median

import numpy as np

ROWS = pickle.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/oc/prod.pkl", "rb"))
ARMS = ["cur", "L1", "L4", "L5", "mkt", "L4all", "selL4", "selL1", "orc"]
WINS = [("confirm", "確認 2026-01〜08（fold B・train<=2025-12）"),
        ("oos25h2", "OOS 2025-07〜12（fold A・train<=2025-06）")]
HIT = ("A_hit", "B_hit", "C_hit", "E_hit", "F_hit")


def kpi(rs, nd):
    rs = [r for r in rs if r is not None]
    n = len(rs)
    if not n:
        return None
    shown = sum(1 for r in rs if r["pay"] >= r["inv"])
    hit = sum(1 for r in rs if r["pay"] > 0)
    pays = [r["pay"] for r in rs if r["pay"] > 0]
    inv = sum(r["inv"] for r in rs)
    pay = sum(r["pay"] for r in rs)
    return dict(n=n, per_day=n / nd, k=np.mean([r["k"] for r in rs]),
                shown=shown / n * 100, hit=hit / n * 100,
                med=median(pays) if pays else 0.0, roi=pay / inv * 100,
                big=sum(1 for r in rs if r["pay"] >= 100_000) / nd,
                gami=sum(1 for r in rs if 0 < r["pay"] < r["inv"]) / max(hit, 1) * 100)


def boot(a, b, n=4000, seed=11):
    pair = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    d = np.array([(1.0 if y["pay"] >= y["inv"] else 0.0)
                  - (1.0 if x["pay"] >= x["inv"] else 0.0) for x, y in pair])
    rng = np.random.default_rng(seed)
    m = len(d)
    o = np.array([d[rng.integers(0, m, m)].mean() for _ in range(n)])
    return (d.mean() * 100, float(np.percentile(o, 2.5)) * 100,
            float(np.percentile(o, 97.5)) * 100, m)


def mdr(a, b):
    keep = dead = save = 0
    for x, y in zip(a, b):
        if x is None or y is None:
            continue
        sx, sy = x["pay"] >= x["inv"], y["pay"] >= y["inv"]
        if sx and sy:
            keep += 1
        elif sx and not sy:
            dead += 1
        elif sy and not sx:
            save += 1
    return keep, dead, save


def show(title, sub, arms=ARMS):
    for win, lab in WINS:
        rs = [r for r in ROWS if r["win"] == win and sub(r)]
        if not rs:
            continue
        nd = len(set(r["date"] for r in rs))
        base = [r["arms"]["cur"] for r in rs]
        print(f"\n--- {title} / {lab}  n={len(rs):,} 営業日={nd} ---")
        print(f"{'腕':<7}{'件/日':>7}{'点数':>6}{'表示的中':>9}{'的中':>8}{'払戻中央':>10}"
              f"{'10万+/日':>9}{'ガミ':>6}{'ROI':>7}  {'Δ表示的中 95%CI':<26}維持/破壊/救済")
        for a in arms:
            cur = [r["arms"].get(a) for r in rs]
            k = kpi(cur, nd)
            if k is None:
                continue
            d, lo, hi, m = boot(base, cur)
            ci = "—" if a == "cur" else f"{d:+.2f} [{lo:+.2f},{hi:+.2f}]"
            kp, dd, sv = mdr(base, cur)
            tail = "" if a == "cur" else f"{kp}/{dd}/{sv}"
            print(f"{a:<7}{k['per_day']:7.2f}{k['k']:6.2f}{k['shown']:8.2f}%"
                  f"{k['hit']:7.2f}%{k['med']:10,.0f}{k['big']:9.3f}"
                  f"{k['gami']:5.1f}%{k['roi']:6.1f}%  {ci:<26}{tail}")
        for a in arms[1:]:
            ch = sum(1 for r in rs
                     if r["arms"].get(a) is not None
                     and [tuple(x) for x in r["arms"][a]["legs"]]
                     != [tuple(x) for x in r["arms"]["cur"]["legs"]])
            print(f"   {a}: 買い目が現行と違う商品 {ch:,} / {len(rs):,} "
                  f"({ch/len(rs)*100:.1f}%)")


def order_rate() -> None:
    print("\n=== 集合を買えていた商品の中で並びも当てた率（三連単・当てにいく商品）===")
    for win, lab in WINS:
        rs = [r for r in ROWS if r["win"] == win and not r["trio"]
              and r["plan"] in HIT and r["arms"]["cur"]["sethit"]]
        if not rs:
            continue
        print(f"  {lab}  n={len(rs):,}")
        for a in ARMS:
            v = [x for x in (r["arms"].get(a) for r in rs) if x is not None]
            print(f"    {a:<7} {sum(1 for x in v if x['pay']>0)/len(v)*100:6.2f}%"
                  f"  (n={len(v):,})")


def reach_report() -> None:
    print("\n=== 並べ替えで到達できる範囲（三連単・当てにいく商品）===")
    for win, lab in WINS:
        rs = [r for r in ROWS if r["win"] == win and not r["trio"] and r["plan"] in HIT]
        n = len(rs)
        miss = [r for r in rs if r["reach"]["miss"]]
        ib = [r for r in miss if r["reach"]["inband"]]
        gt = [r for r in miss if r["reach"]["gate"]]
        print(f"  {lab}  三連単商品 n={n:,}")
        print(f"    ②順序違い（集合は買えた・並びで外した）  {len(miss):,}"
              f"  ({len(miss)/n*100:.2f}% of 商品)")
        print(f"    └ 正解の並びが帯の中                    {len(ib):,}"
              f"  ({len(ib)/max(len(miss),1)*100:.1f}% of ②)")
        print(f"    └ 入れ替えても入稿ゲートを通る（＝直せる） {len(gt):,}"
              f"  ({len(gt)/max(len(miss),1)*100:.1f}% of ②)"
              f"  → 表示的中 +{len(gt)/n*100:.2f}pt が並べ替えの天井")


def main() -> None:
    show("ラインナップ全体", lambda r: True)
    show("三連単の当てにいく商品", lambda r: r["plan"] in HIT)
    show("B_hit+F_hit", lambda r: r["plan"] in ("B_hit", "F_hit"))
    show("B_hit", lambda r: r["plan"] == "B_hit")
    show("F_hit", lambda r: r["plan"] == "F_hit")
    show("C_hit", lambda r: r["plan"] == "C_hit")
    show("E_hit", lambda r: r["plan"] == "E_hit")
    show("A_hit", lambda r: r["plan"] == "A_hit")
    order_rate()
    reach_report()


if __name__ == "__main__":
    main()
