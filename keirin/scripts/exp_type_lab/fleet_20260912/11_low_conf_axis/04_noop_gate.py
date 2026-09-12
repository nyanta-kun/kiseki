#!/usr/bin/env python3
"""(a) B群「軸の再指名」が本当に買い目を動かすのか（プラン別）
(b) 入稿ゲート通過率（プール × 層）"""
from __future__ import annotations
import collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib as L

rows, thr = L.load()
SWAPS = ("sw_a2", "sw_pw", "sw_mkt", "sw_hon", "sw_a2dis")
POOLS = ("all", "hd_not_a1", "hd_a2", "hd_a23", "hd_mkt", "hd_pw",
         "bust", "bust_pw", "bust_mkt")

print("=== (a) 軸の再指名が買い目を変えたレース（％）— プラン別 ===")
print("  プランの `structure` が軸(order)を使わないなら定義上 no-op。")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win]
    plans = sorted({r["base"]["plan"] for r in sub})
    print(f"\n[{win}] n={len(sub):,}")
    print("  {:10s} {:>7s} ".format("plan", "n") +
          " ".join(f"{s:>12s}" for s in SWAPS))
    for p in plans + ["(全体)"]:
        s = sub if p == "(全体)" else [r for r in sub if r["base"]["plan"] == p]
        cells = []
        for sw in SWAPS:
            nd = sum(1 for r in s
                     if (a := r["arms"].get(sw)) is not None
                     and not a.get("noop")
                     and (a["k"] != r["base"]["k"]
                          or abs(a["pay"] - r["base"]["pay"]) > 1e-6
                          or abs(a["mean"] - r["base"]["mean"]) > 1.0))
            cells.append(f"{nd/len(s)*100:11.2f}%")
        print(f"  {p:10s} {len(s):7d} " + " ".join(cells))

print("\n=== (b) 入稿ゲート通過率（現行プラン × プール）===")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win]
    print(f"\n[{win}] 母集団 n={len(sub):,}（型判定できた7車レース）")
    print("  {:12s} {:>9s} {:>9s} {:>9s} {:>9s}".format(
        "プール", "組めた%", "ゲート通過%", "平均点数", "計画払戻中央"))
    for pn in POOLS:
        got = [r["arms"][pn] for r in sub if pn in r["arms"]]
        if not got:
            continue
        ga = [a for a in got if a["gate"]]
        med = sorted(a["mean"] for a in ga)
        print(f"  {pn:12s} {len(got)/len(sub)*100:9.1f} {len(ga)/len(sub)*100:9.1f}"
              f" {sum(a['k'] for a in ga)/max(len(ga),1):9.2f}"
              f" {med[len(med)//2] if med else 0:9,.0f}")

print("\n=== (c) signboard@T のゲート通過率 ===")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win]
    print(f"\n[{win}]")
    Ts = (20_000, 30_000, 150_000, 400_000)
    print("  {:12s} ".format("プール") + " ".join(f"{t:>10,d}" for t in Ts))
    for pn in ("all", "bust", "bust_pw", "hd_not_a1", "hd_a2", "hd_mkt"):
        cells = []
        for t in Ts:
            k = f"S:{pn}@{t}"
            g = sum(1 for r in sub if (a := r["arms"].get(k)) and a["gate"])
            cells.append(f"{g/len(sub)*100:9.1f}%")
        print(f"  {pn:12s} " + " ".join(cells))

print("\n=== (d) 決着が各プールに入っている率（＝そのプールの的中上限）===")
for win in ("explore", "confirm"):
    sub = [r for r in rows if r["win"] == win and not r["trio"]]
    print(f"\n[{win}] 三連単プランのみ n={len(sub):,}")
    print("  {:18s} ".format("層") + " ".join(f"{p:>11s}" for p in POOLS))
    for nm in ["(全体)", "axis_sum_lo25", "p3_gap12_lo25", "pw_gap12_lo25",
               "pw_max_lo25", "pw_ent_hi10", "dis", "axis_sum_lo25+dis"]:
        s = [r for r in sub if L.inlay(r, nm)]
        if len(s) < 30:
            continue
        cells = [f"{sum(1 for r in s if r['inpool'].get(p))/len(s)*100:10.2f}%"
                 for p in POOLS]
        print(f"  {nm:18s} " + " ".join(cells))
