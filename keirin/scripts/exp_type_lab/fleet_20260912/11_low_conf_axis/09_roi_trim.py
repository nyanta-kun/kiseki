#!/usr/bin/env python3
"""低信頼層での bust の ROI 優位が「裾」に依存していないかを払戻上限で確かめる。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib as L
import numpy as np

rows, thr = L.load()
print("=== 低信頼層での bust の ROI 優位はどれだけ裾に依存するか ===")
for lay in ("axis_sum_lo25", "pw_gap12_lo25", "pw_max_lo25"):
    for win in ("explore", "confirm"):
        sub = [r for r in rows if r["win"] == win and L.inlay(r, lay)]
        pr = [(r["arms"]["all"], r["arms"]["bust"]) for r in sub
              if "all" in r["arms"] and "bust" in r["arms"]
              and r["arms"]["all"]["gate"] and r["arms"]["bust"]["gate"]]
        for cap in (None, 300_000, 100_000, 50_000):
            b = np.array([min(x[0]["pay"], cap) if cap else x[0]["pay"] for x in pr])
            a = np.array([min(x[1]["pay"], cap) if cap else x[1]["pay"] for x in pr])
            ib = np.array([x[0]["inv"] for x in pr]); ia = np.array([x[1]["inv"] for x in pr])
            print(f"  {lay:15s} {win:8s} cap={str(cap):>7s}  現行 ROI {b.sum()/ib.sum()*100:6.1f}"
                  f"  bust ROI {a.sum()/ia.sum()*100:6.1f}"
                  f"  Δ {a.sum()/ia.sum()*100-b.sum()/ib.sum()*100:+6.1f}")
        ap = sorted((x[1]["pay"] for x in pr), reverse=True)
        tot = sum(ap)
        print(f"    → bust の払戻: 上位10件で {sum(ap[:10])/tot*100:.1f}% / "
              f"上位1%件で {sum(ap[:max(1,len(pr)//100)])/tot*100:.1f}%  "
              f"的中件数 {sum(1 for x in ap if x > 0)}")
