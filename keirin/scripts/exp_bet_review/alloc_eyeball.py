"""①の目視: 2025 の1日ぶんの商品構成と、本番の直近日（netkeirin_submissions）の rank_key 別件数を並べる。"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C
import numpy as np

recs, start = C.load_base()
morning = C.morning_set(start)
days = C.by_day(recs)
L = C.lineup(recs, morning)
# 年間の1日あたり
tot = Counter(); nd = len(days)
for d, (sold, _) in L.items():
    for s in sold:
        tot[(s["plan"], s["slot"])] += 1
print(f"2025 母集団 {len(recs)}R / {nd}日 / 1日あたり {len(recs)/nd:.1f}R(7車)")
print("①の商品別 件/日:", {f"{p}({sl})": round(n / nd, 2) for (p, sl), n in sorted(tot.items(), key=lambda x: -x[1])})
print("①合計 件/日", round(sum(tot.values()) / nd, 2), " 穴商品 件/日", round(sum(n for (p, _), n in tot.items() if C.is_ana(p)) / nd, 2))
# 代表日（7車が60R前後ある日）
cands = sorted(days, key=lambda d: abs(len(days[d]) - 48))
for d in cands[:2]:
    sold, status = L[d]
    print(f"\n=== {d}  7車 {len(days[d])}R → 商品 {len(sold)} 本")
    print("  プラン別:", dict(Counter(s["plan"] for s in sold)))
    print("  スロット別:", dict(Counter(s["slot"] for s in sold)))
    print("  穴本数", sum(C.is_ana(s["plan"]) for s in sold), " 見送り内訳", dict(Counter(v for v in status.values())))
    for s in sorted(sold, key=lambda s: s["key"])[:6]:
        print(f"    {s['key']} {s['plan']:<7}{s['slot']:<8} 賭け金{s['inv']:>6,.0f} 計画平均払戻{s['mean']:>8,.0f} 払戻{s['pay']:>8,.0f}")
