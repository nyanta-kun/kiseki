#!/usr/bin/env python3
"""腕⓪ 供給在庫の実測 — 制約（波ごとの日次上限・高額枠）を本番同一で再現する。"""
from __future__ import annotations
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

for win in ("explore", "confirm"):
    nd = L.ndays(win)
    rows = [r for r in L.load() if r["win"] == win]
    print(f"\n{'='*100}\n[{win}] レース {len(rows):,} / {nd} 日 = {len(rows)/nd:.2f} R/日"
          f"（型判定できた7車のみ）")
    inv = L.inventory(win)
    c = Counter(k for _r, k, _w in inv)
    print(f"  棄却の内訳: " + "  ".join(
        f"{k}={c[k]/nd:.2f}件/日({c[k]:,})" for k in ("axis", "egate", "cap")))
    # 現行の入稿（腕①）
    cur = L.simulate(win, hp_slots=5, hp_types=("B", "C", "D"), supply=("cap",))
    o = Counter(r["origin"] for r in cur)
    print(f"  現行の入稿: {len(cur)/nd:.2f}件/日"
          f"（通常 {o['normal']/nd:.2f} ＋ 枠外 {o['exempt']/nd:.2f}"
          f" ＋ 高額枠 {o['hp:cap']/nd:.2f}）")
    print(L.HEAD); print(L.line("① 現行", L.kpi(cur, nd)))

    # 波別
    print("\n  ── 波別 ──")
    print("    {:8s} {:>7s} {:>9s} {:>9s} {:>9s} {:>9s}".format(
        "波", "R/日", "判定対象/日", "上限落ち/日", "軸ゲート落ち/日", "入稿ゲート落ち/日"))
    for wave in ("morning", "noon", "night"):
        wr = [r for r in rows if r["wave"] == wave]
        nj = defaultdict(int)
        for r in wr:
            if not r["exempt"]:
                nj[r["date"]] += 1
        cc = Counter(k for _r, k, w in inv if w == wave)
        print(f"    {wave:8s} {len(wr)/nd:7.2f} {sum(nj.values())/nd:11.2f}"
              f" {cc['cap']/nd:11.2f} {cc['axis']/nd:13.2f} {cc['egate']/nd:15.2f}")

    # 型別
    print("\n  ── 在庫の型分布（件/日 と 構成%）──")
    print("    {:14s} {:>28s}   {:>28s}".format("", "上限落ち", "軸ゲート落ち"))
    print("    {:14s} ".format("型") + "  ".join(f"{t:>5s}" for t in "ABCDEF")
          + "  |  " + "  ".join(f"{t:>5s}" for t in "ABCDEF"))
    for kind in ("cap", "axis"):
        pass
    capt = Counter(r["type"] for r, k, _w in inv if k == "cap")
    axt = Counter(r["type"] for r, k, _w in inv if k == "axis")
    print("    {:14s} ".format("件/日") + "  ".join(f"{capt[t]/nd:5.2f}" for t in "ABCDEF")
          + "  |  " + "  ".join(f"{axt[t]/nd:5.2f}" for t in "ABCDEF"))
    tc, ta = max(sum(capt.values()), 1), max(sum(axt.values()), 1)
    print("    {:14s} ".format("構成%") + "  ".join(f"{capt[t]/tc*100:5.1f}" for t in "ABCDEF")
          + "  |  " + "  ".join(f"{axt[t]/ta*100:5.1f}" for t in "ABCDEF"))
    print(f"    B/C/D の割合: 上限落ち {sum(capt[t] for t in 'BCD')/tc*100:.1f}%"
          f" / 軸ゲート落ち {sum(axt[t] for t in 'BCD')/ta*100:.1f}%")
    print(f"    型F の割合  : 上限落ち {capt['F']/tc*100:.1f}%"
          f" / 軸ゲート落ち {axt['F']/ta*100:.1f}%")

    # 高額枠の候補として実際に出せる本数（型と入稿ゲートを通ったもの）
    print("\n  ── 高額枠の候補として実際に出せる在庫（型ゲート＋入稿ゲート通過後）──")
    for label, types in (("B/C/D 限定", ("B", "C", "D")), ("全6型", tuple("ABCDEF"))):
        for kind in ("cap", "axis", "cap+axis"):
            ks = ("cap", "axis") if kind == "cap+axis" else (kind,)
            n = 0; nsign = 0
            for r, k, _w in inv:
                if k not in ks or r["type"] not in types:
                    continue
                n += 1
                if L._hp_pick(r, 0, "alt") is not None:
                    nsign += 1
            print(f"    {label:12s} {kind:9s} 在庫 {n/nd:6.2f}件/日 → "
                  f"入稿ゲート通過 {nsign/nd:6.2f}件/日")
