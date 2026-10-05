"""①（版A）の商品別の実測: 件/日・平均点数・計画払戻中央・表示的中・回収率。H08/H09 の前提表用。"""
import sys
from pathlib import Path
from collections import defaultdict
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alloc_common as C
recs, start = C.load_base(); morning = C.morning_set(start)
L = C.lineup(recs, morning)
g = defaultdict(list)
for d, (sold, _) in L.items():
    for s in sold: g[(s["plan"], s["slot"])].append(s)
nd = len(L)
print("| プラン(スロット) | 穴商品 | 件/日 | 平均点数 | 計画平均払戻の中央(円) | 表示的中% | 回収率% | 10万+(件/年) |")
print("|---|---|---|---|---|---|---|---|")
for (p, sl), xs in sorted(g.items(), key=lambda kv: -len(kv[1])):
    inv = sum(x["inv"] for x in xs); pay = sum(x["pay"] for x in xs)
    print(f"| {p} ({sl}) | {'穴' if C.is_ana(p) else '-'} | {len(xs)/nd:.2f} | {np.mean([x['n'] for x in xs]):.1f} | {np.median([x['mean'] for x in xs]):,.0f} | "
          f"{sum(x['pay']>x['inv'] for x in xs)/len(xs)*100:.1f} | {pay/inv*100:.1f} | {sum(x['pay']>=100000 for x in xs)} |")
