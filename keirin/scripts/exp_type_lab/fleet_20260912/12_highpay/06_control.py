#!/usr/bin/env python3
"""腕② × 腕⑤ — 層で絞る案を「同数を無作為に選ぶ対照 20seed」と比べる。

🔴 BRIEF の鉄則「件数を減らす腕には必ず無作為対照 20seed」。
   ここでは**在庫のどの部分を高額枠へ回すか**が腕なので、対照は
   「同数を在庫から無作為に選ぶ」。層の資格判定を確率 `rand_frac` に置き換える。
"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

ALL6 = tuple("ABCDEF")
LAM = float(sys.argv[1]) if len(sys.argv) > 1 else 0.50
PROD = sys.argv[2] if len(sys.argv) > 2 else "bust15"
SEEDS = 20


def fill(recs):
    c = Counter(r["origin"] for r in recs)
    return c["hp:cap"] + c["hp:axis"]


for win in ("explore", "confirm"):
    nd = L.ndays(win); days = sorted({r["date"] for r in L.load() if r["win"] == win})
    print(f"\n{'='*118}\n[{win}] λ={LAM} 商品={PROD}  {nd}日\n{'='*118}")
    full = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                      frac=LAM, product=PROD)
    print(L.HEAD)
    print(L.line("全在庫（層で絞らない・10本）", L.kpi(full, nd))
          + f"  |枠 {fill(full)/nd:5.2f}/日")
    for layer in ("pw_gap12_lo25", "axis_sum_lo25", "p3_gap12_lo25", "pw_ent_hi10"):
        arm = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                         frac=LAM, product=PROD, layer=layer)
        f = fill(arm)
        print("\n" + L.line(f"② 層={layer}", L.kpi(arm, nd)) + f"  |枠 {f/nd:5.2f}/日")
        # 対照の rand_frac を件数一致で二分探索
        lo, hi = 0.0, 1.0
        for _ in range(18):
            mid = (lo + hi) / 2
            got = fill(L.simulate(win, hp_slots=10, hp_types=ALL6,
                                  supply=("cap", "axis"), frac=LAM, product=PROD,
                                  rng=np.random.default_rng(777), rand_frac=mid))
            if got < f:
                lo = mid
            else:
                hi = mid
        rf = (lo + hi) / 2
        cb, cs, cf = [], [], []
        for s in range(SEEDS):
            c = L.simulate(win, hp_slots=10, hp_types=ALL6, supply=("cap", "axis"),
                           frac=LAM, product=PROD,
                           rng=np.random.default_rng(1000 + s), rand_frac=rf)
            k = L.kpi(c, nd); cb.append(k["big"]); cs.append(k["shown"]); cf.append(fill(c) / nd)
        k = L.kpi(arm, nd)
        wb = sum(1 for x in cb if k["big"] > x); ws = sum(1 for x in cs if k["shown"] > x)
        print(f"     ⑤ 無作為対照20seed (rand_frac={rf:.3f}・枠 中央{np.median(cf):.2f}/日): "
              f"10万+/日 中央 {np.median(cb):.3f} → 腕の勝ち {wb}/20   "
              f"表示的中 中央 {np.median(cs):.2f}% → 勝ち {ws}/20")
