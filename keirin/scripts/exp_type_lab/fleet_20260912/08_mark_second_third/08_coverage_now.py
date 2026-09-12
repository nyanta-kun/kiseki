#!/usr/bin/env python3
"""現行の入稿商品は、どの決着型をどれだけ買えているか（ユーザー観察の検算）。"""
from __future__ import annotations
import sys
from statistics import median
from importlib import import_module
sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
                   "da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/"
                   "08_mark_second_third")
C3 = import_module("03_cmp")
ROWS, NDAYS, WINS = C3.ROWS, C3.NDAYS, C3.WINS

CLS = (("1着が◎か○", lambda r: not r["first_out"]),
       ("1着◎○以外・◎○とも2・3着", lambda r: r["first_out"] and r["n23"] == 2),
       ("1着◎○以外・◎○の1車が2・3着", lambda r: r["first_out"] and r["n23"] == 1),
       ("1着◎○以外・◎○とも圏外", lambda r: r["first_out"] and r["n23"] == 0))
for lbl, win in WINS:
    rs = [r for r in ROWS if r["win"] == win and r["base"]
          and r["base"]["gate"] and r["base"]["axis_ok"]]
    nd = NDAYS[win]
    print(f"\n=== {lbl} 入稿した商品 {len(rs):,}件（{len(rs)/nd:.2f}件/日）===")
    print(f"  {'決着の型':30s} {'件数':>7s} {'割合':>7s} {'買えていた%':>11s} "
          f"{'その払戻中央(倍)':>16s} {'100倍+の決着%':>13s}")
    for nm, f in CLS:
        g = [r for r in rs if f(r)]
        if not g:
            continue
        inb = [r for r in g if r["base"]["hit_in"]]
        pays = sorted(r["pay_tf"] for r in g)
        print(f"  {nm:30s} {len(g):7,d} {len(g)/len(rs)*100:6.2f}% "
              f"{len(inb)/len(g)*100:10.2f}% {median(pays):15,.0f} "
              f"{sum(1 for x in pays if x>=100)/len(pays)*100:12.2f}%")
    # 100倍以上の決着に限る
    big = [r for r in rs if r["pay_tf"] >= 100]
    print(f"\n  -- 100倍以上（万車券）で決まった {len(big):,}件"
          f"（{len(big)/len(rs)*100:.2f}%・{len(big)/nd:.2f}件/日）の内訳 --")
    for nm, f in CLS:
        g = [r for r in big if f(r)]
        if not g:
            continue
        inb = [r for r in g if r["base"]["hit_in"]]
        print(f"  {nm:30s} {len(g):7,d} {len(g)/len(big)*100:6.2f}% "
              f"{len(inb)/len(g)*100:10.2f}%")
