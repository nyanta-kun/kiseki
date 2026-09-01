#!/usr/bin/env python3
"""提案B ②: 三連複で配当が付くレースだけ振り替えるハイブリッド（2026-08-31）。

三連複k点が入稿ゲート（平均想定払戻>2万円）を通れば三連複、通らなければ A_hit。
現行（A_hit のみ）と**同一レースで**対応のあるブートストラップで比べる。
"""
import sys
from pathlib import Path, itertools, random
from statistics import median
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, type_a_upset2 as M
data = M.load()

def mk_trio(n):
    return lambda d: ("trio", [frozenset((d["o"][0], d["o"][1], d["o"][k]))
                               for k in range(2, 2 + n)])
for n in (1, 2, 3):
    M.ARMS[f"三連複{n}点"] = mk_trio(n)

def hybrid(d, trio_arm):
    """三連複が通れば三連複、通らなければ A_hit。どちらも通らなければ売らない。"""
    r = M.play(d, trio_arm)
    if r: r = dict(r, kind="trio")
    else:
        r = M.play(d, "A_hit 現行3点")
        if r: r = dict(r, kind="tf")
    return r

def summ2(recs, nd):
    s = M.summ(recs, nd)
    if s: s["trio_share"] = sum(1 for r in recs if r.get("kind") == "trio") / len(recs) * 100
    return s

def paired(rs, fa, fb, n=1500, seed=0):
    pair = [(fa(d), fb(d)) for d in rs]
    pair = [(x, y) for x, y in pair if x and y]
    rnd = random.Random(seed); m = len(pair)
    dr, ds = [], []
    for _ in range(n):
        ia = ib = pa = pb = 0.0; sa = sb = 0
        for _ in range(m):
            j = rnd.randrange(m); x, y = pair[j]
            ia += x["inv"]; pa += x["pay"]; sa += x["pay"] > x["inv"]
            ib += y["inv"]; pb += y["pay"]; sb += y["pay"] > y["inv"]
        dr.append(pb/ib*100 - pa/ia*100); ds.append((sb-sa)/m*100)
    dr.sort(); ds.sort()
    return m, (ds[int(n*.025)], ds[int(n*.975)]), (dr[int(n*.025)], dr[int(n*.975)])

for win, (lo, hi) in M.WINDOWS.items():
    rs = [d for d in data if lo <= d["date"] <= hi]
    nd = len({d["date"] for d in rs})
    print(f"\n{'='*126}\n=== {win}  型A {len(rs):,}R / {nd}日 ===")
    print(M.HDR + "  三連複%")
    base = [r for r in (M.play(d, "A_hit 現行3点") for d in rs) if r]
    print(M.row("現行（A_hit のみ）", M.summ(base, nd)))
    plans = {}
    for arm in ("三連複1点", "三連複2点", "三連複3点"):
        recs = [r for r in (hybrid(d, arm) for d in rs) if r]
        plans[arm] = recs
        s = summ2(recs, nd)
        print(M.row(f"ハイブリッド({arm})", s) + f"{s['trio_share']:>8.1f}")
    print(f"\n  ── 同じレースでの直接対決（現行 → ハイブリッド）──")
    for arm in ("三連複1点", "三連複2点", "三連複3点"):
        m, ds, dr = paired(rs, lambda d: M.play(d, "A_hit 現行3点"),
                           (lambda a: lambda d: hybrid(d, a))(arm))
        print(f"    ハイブリッド({arm:<7}) n={m:>5,}  "
              f"表示的中の差 CI[{ds[0]:+.2f},{ds[1]:+.2f}]pt   ROIの差 CI[{dr[0]:+.1f},{dr[1]:+.1f}]pt")
