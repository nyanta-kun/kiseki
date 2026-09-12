#!/usr/bin/env python3
"""①計画 §1 の数字の検算（上限なしで軸ゲート落ちを全部売る腕）②充填率の日次分布
③10万+ の出どころ ④高額枠の型構成。"""
from __future__ import annotations
import sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

ALL6 = tuple("ABCDEF"); BCD = ("B", "C", "D")

print("=== ① 計画 §1 の検算（**上限を掛けず** 軸ゲート落ち全量を bust@15万で純増）===")
print("   計画の見込み: 45.75件/日 / 表示的中 21.75% / 10万+ 0.708 / 30万+ 0.051 / ROI 84.2")
print(L.HEAD)
for win in ("explore", "confirm"):
    nd = L.ndays(win)
    print(f"  [{win}]")
    print(L.line("  上限なし・現行のみ", L.kpi(L.simulate(win, hp_slots=0, frac=0.0), nd)))
    r = L.simulate(win, hp_slots=99, hp_types=ALL6, supply=("axis",), frac=0.0,
                   product="bust15")
    print(L.line("  上限なし・+軸ゲート落ち全量", L.kpi(r, nd)))
    r2 = L.simulate(win, hp_slots=99, hp_types=ALL6, supply=("axis",), frac=0.0,
                    product="sign")
    print(L.line("  （参考）sign@15万で純増", L.kpi(r2, nd)))

print("\n\n=== ②③④ 推奨腕の内訳（λ=0.50 と 0.35・10本・全6型・cap+axis・bust15）===")
for lam in (0.50, 0.35):
    for win in ("explore", "confirm"):
        nd = L.ndays(win)
        r = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                       frac=lam, product="bust15")
        hp = [x for x in r if x["origin"].startswith("hp:")]
        per = Counter(x["date"] for x in hp)
        days = sorted({x["date"] for x in L.load() if x["win"] == win})
        cnt = np.array([per.get(d, 0) for d in days])
        print(f"\n  [λ={lam} {win}]  高額枠 {len(hp)/nd:.2f}本/日  充填率 {len(hp)/nd/10*100:.1f}%")
        print(f"    枠の埋まり方: 10本埋まった日 {np.mean(cnt>=10)*100:.1f}% / "
              f"7本以上 {np.mean(cnt>=7)*100:.1f}% / 5本以上 {np.mean(cnt>=5)*100:.1f}% / "
              f"0本 {np.mean(cnt==0)*100:.1f}%  （中央 {int(np.median(cnt))}本）")
        tc = Counter(x["type"] for x in hp)
        print("    型構成: " + "  ".join(
            f"{t}={tc[t]/max(len(hp),1)*100:4.1f}%({tc[t]/nd:.2f}/日)" for t in "ABCDEF"))
        oc = Counter(x["origin"] for x in hp)
        print(f"    供給源: 上限落ち {oc['hp:cap']/nd:.2f}/日  軸ゲート落ち {oc['hp:axis']/nd:.2f}/日")
        bigs = [x for x in r if x["pay"] >= 100_000]
        bc = Counter("高額枠" if x["origin"].startswith("hp:") else
                     ("枠外" if x["origin"] == "exempt" else "通常") for x in bigs)
        print(f"    10万+ の出どころ: " + "  ".join(
            f"{k}={v/nd:.3f}/日" for k, v in sorted(bc.items())))
        bt = Counter(x["type"] for x in bigs if x["origin"].startswith("hp:"))
        print("    10万+（高額枠）の型: " + "  ".join(
            f"{t}={bt[t]/nd:.3f}" for t in "ABCDEF"))
