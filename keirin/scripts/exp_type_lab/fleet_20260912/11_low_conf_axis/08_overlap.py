#!/usr/bin/env python3
"""既存商品との重複 — 層 × `A_ana` / `{型}_big` の射程。"""
from __future__ import annotations
import sys, collections
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import lib as L

rows, thr = L.load()
ANA = 1.4076
LAY = ("axis_sum_lo25", "p3_gap12_lo25", "pw_gap12_lo25", "pw_max_lo25",
       "pw_ent_hi10", "dis")

for win in ("explore", "confirm"):
    rs = [r for r in rows if r["win"] == win]
    sold = [r for r in rs if r["base"]["gate"] and r["base"]["axis_ok"]]
    ana = {r["key"] for r in rs if r["type"] == "A" and r["pw_ent"] >= ANA}
    big = {r["key"] for r in rs if r["type"] in "BCD"}
    print(f"\n=== [{win}] 層と既存の一撃/高額枠の重なり（母集団 n={len(rs):,}）===")
    print("  {:16s} {:>7s} {:>10s} {:>12s} {:>12s} {:>26s}".format(
        "層", "n", "A_ana と", "{型}_big と", "F_sign 型F", "型構成 A/B/C/D/E/F %"))
    for nm in LAY:
        s = [r for r in rs if L.inlay(r, nm)]
        ks = {r["key"] for r in s}
        tc = collections.Counter(r["type"] for r in s)
        comp = "/".join(f"{tc.get(t,0)/len(s)*100:.0f}" for t in "ABCDEF")
        print(f"  {nm:16s} {len(s):7d} {len(ks&ana)/len(ks)*100:9.2f}% "
              f"{len(ks&big)/len(ks)*100:11.2f}% "
              f"{tc.get('F',0)/len(s)*100:11.2f}% {comp:>26s}")
    print(f"  参考 A_ana の母集団 n={len(ana):,}  ({len(ana)/L.ndays(win):.2f}件/日)")
    print(f"  型A で axis_sum の最小 = "
          f"{min(r['axis_sum'] for r in rs if r['type']=='A'):.4f}  "
          f"↔ axis_sum_lo25 の上限 = {thr['axis_sum_lo25'][2]:.4f}"
          f"  → 重なりは構造上 0")

print("\n=== `A_ana` が取っている範囲での3者対決（基準 = 現行 A_ana）===")
for win in ("explore", "confirm"):
    rs = [r for r in rows if r["win"] == win and r["base"]["plan"] == "A_ana"]
    nd = L.ndays(win)
    print(f"\n[{win}] n={len(rs):,}")
    print(L.HEAD)
    print(L.line("現行 A_ana(bust_top5)", L.summ([r["base"] for r in rs], nd)))
    for a in ("all", "hd_not_a1", "hd_a2", "bust",
              "S:all@150000", "S:bust@150000", "S:hd_a2@150000", "S:bust@400000"):
        pr = [(r["base"], r["arms"][a]) for r in rs
              if a in r["arms"] and r["arms"][a]["gate"]]
        if len(pr) < 40:
            continue
        b = [x[0] for x in pr]; ar = [x[1] for x in pr]
        print(L.line(a, L.summ(ar, nd)))
        ds, lo, hi = L.paired(L.shown_vec(b), L.shown_vec(ar))
        db, blo, bhi = L.paired(L.big_vec(b), L.big_vec(ar))
        print(f"      Δ表示的中 {ds:+6.2f} [{lo:+6.2f},{hi:+6.2f}]"
              f"  Δ10万+率 {db:+5.2f} [{blo:+5.2f},{bhi:+5.2f}] (n={len(pr)})")
