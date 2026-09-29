"""安い決着の目標引き下げ（本番コード）を1日の商品構成全体で測る（日次上限・高額枠込み）。"""
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lineup_arms as LA
import src.type_lab as TL

cache = pickle.load(open(os.environ.get("TYPE_LAB_CTX", "/tmp/type_lab_ctx_fixed.pkl"), "rb"))
ON = dict(TL.CHEAP_SHARE_MIN)
W = {"探索": ("2024-07-01", "2025-12-31"), "確認": ("2026-01-01", "2026-08-04")}
out = {}
for arm in ("現行", "本案"):
    TL.CHEAP_SHARE_MIN.clear()
    if arm == "本案": TL.CHEAP_SHARE_MIN.update(ON)
    for w, (a, b) in W.items():
        idx = [i for i, x in cache.items() if x is not None and a <= x.date <= b]
        out[(arm, w)] = LA.run(arm, {}, idx, cache)
pickle.dump(out, open(str(Path(__file__).resolve().parent / "ct_lineup.pkl"), "wb"))
def boot(ra, rb, key, B=1500):
    da, db = defaultdict(list), defaultdict(list)
    for r in ra: da[r["day"]].append(r)
    for r in rb: db[r["day"]].append(r)
    days = sorted(set(da) | set(db)); rng = np.random.default_rng(0); v = []
    def f(rs, n):
        if key == "shown": return 100 * sum(r["pay"] > r["inv"] for r in rs) / len(rs)
        if key == "roi": return 100 * sum(r["pay"] for r in rs) / sum(r["inv"] for r in rs)
        return sum(r["pay"] >= 40000 and r["pay"] > r["inv"] for r in rs) / n
    for _ in range(B):
        pick = [days[j] for j in rng.integers(0, len(days), len(days))]
        A = [r for d in pick for r in da[d]]; Bb = [r for d in pick for r in db[d]]
        v.append(f(A, len(pick)) - f(Bb, len(pick)))
    return np.percentile(v, [2.5, 97.5])
for w in W:
    nd = len({r["day"] for r in out[("現行", w)]})
    print(f"\n==== {w}（{nd}日）\n{LA.HEAD}")
    for arm in ("現行", "本案"):
        print(LA.line(arm, LA.summarize(out[(arm, w)], nd)))
    for grp in (None, "A_hit", "B_hit"):
        ra = [r for r in out[("本案", w)] if grp is None or r["plan"] == grp]
        rb = [r for r in out[("現行", w)] if grp is None or r["plan"] == grp]
        s = [boot(ra, rb, k) for k in ("shown", "roi", "b4")]
        print(f"  Δ {grp or '全商品'}: 表示的中 [{s[0][0]:+.2f},{s[0][1]:+.2f}]  ROI [{s[1][0]:+.1f},{s[1][1]:+.1f}]  4万+/日 [{s[2][0]:+.3f},{s[2][1]:+.3f}]")
    for p in ("A_hit", "B_hit"):
        for arm in ("現行", "本案"):
            print(LA.line(f"{p} {arm}", LA.summarize([r for r in out[(arm, w)] if r["plan"] == p], nd)))
    # 救済/破壊（同じレース同じ枠）
    ka = {(r["race_key"], r["slot"]): r for r in out[("本案", w)]}; kb = {(r["race_key"], r["slot"]): r for r in out[("現行", w)]}
    both = set(ka) & set(kb); h = lambda r: r["pay"] > r["inv"]
    print(f"  件数 現行{len(kb)} 本案{len(ka)} 共通{len(both)}  救済{sum(h(ka[k]) and not h(kb[k]) for k in both)} 破壊{sum(h(kb[k]) and not h(ka[k]) for k in both)}  買い目が変わった{sum(ka[k]['stakes'] != kb[k]['stakes'] for k in both)}")
