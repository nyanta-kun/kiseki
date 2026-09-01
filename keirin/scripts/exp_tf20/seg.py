#!/usr/bin/env python3
"""開催日目（初日/最終日）と種別で三連単枠は変わるか（2026-08-25・ユーザー仮説）。

> ここには開催日数(初日、最終日)なども影響あるのかもしれない
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

Z = np.load("/tmp/tf20_board.npz", allow_pickle=True)
PROB, PO = Z["PROB"].astype(np.float64), Z["PO"].astype(np.float64)
WIN, PAY = Z["WIN"], Z["PAY"]
DATE, DAYIDX, RTYPE = Z["DATE"].astype(str), Z["DAYIDX"], Z["RTYPE"].astype(str)
GRADE = Z["GRADE"].astype(str)
ok = WIN >= 0
PROB, PO, WIN, PAY, DATE, DAYIDX, RTYPE, GRADE = (
    a[ok] for a in (PROB, PO, WIN, PAY, DATE, DAYIDX, RTYPE, GRADE))
EXP = DATE < "2026-01-01"

LO, K = 30, 3
band = PO >= LO
sc = np.where(band, PROB, -1.0)
top = np.argsort(-sc, axis=1)[:, :K]
valid = np.take_along_axis(band, top, 1)
HIT = ((top == WIN[:, None]) & valid).any(1)
NPT = valid.sum(1)
ST = np.array([max(100, (10000 // max(n, 1)) // 100 * 100) for n in NPT])
BET = np.where(NPT > 0, ST * NPT, 0)
PAYV = np.where(HIT, PAY * ST / 100.0, 0.0)


def blk(mask, B=3000, seed=7):
    """日ブロック bootstrap で ROI の 95%CI。"""
    d = defaultdict(lambda: [0.0, 0.0])
    for i in np.flatnonzero(mask & (NPT > 0)):
        d[DATE[i]][0] += BET[i]; d[DATE[i]][1] += PAYV[i]
    a = np.array(list(d.values()))
    if len(a) < 5:
        return 0, 0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(B, len(a)))
    r = np.sort(a[idx, 1].sum(1) / np.maximum(a[idx, 0].sum(1), 1))
    return r[int(B * .025)], r[int(B * .975)]


def row(mask, label):
    m = mask & (NPT > 0)
    n = int(m.sum())
    if n < 200:
        return None
    lo, hi = blk(mask)
    hp = PAYV[m & HIT]
    return (f"{label:<16}{n:>7,}{HIT[m].mean():>8.2%}"
            f"{PAYV[m].sum()/max(BET[m].sum(),1):>8.1%}"
            f"  [{lo:>5.1%},{hi:>5.1%}]"
            f"{(np.median(hp) if len(hp) else 0):>10,.0f}"
            f"{(PAYV[m]>=100000).mean()*100:>9.2f}%")


print(f"【{LO}倍以上 {K}点(確率順)】壁 = 74.85%")
for per, base in (("探索(〜2025-12)", EXP), ("確認(2026-)", ~EXP)):
    print(f"\n── {per} ──")
    print(f"{'区分':<16}{'R':>7}{'的中%':>8}{'ROI':>8}{'  95%CI':>16}"
          f"{'払戻中央':>10}{'10万+率':>10}")
    for di in (1, 2, 3, 4):
        r = row(base & (DAYIDX == di), f"開催{di}日目")
        if r: print(r)
    print()
    for rt in sorted(set(RTYPE)):
        r = row(base & (RTYPE == rt), rt or "(空)")
        if r: print(r)
    print()
    fin = np.isin(RTYPE, ["決勝", "チャレンジ決勝"])
    for lab, m in (("決勝系", fin), ("決勝系以外", ~fin),
                   ("最終日", DAYIDX >= 3), ("初日", DAYIDX == 1),
                   ("最終日×決勝系", (DAYIDX >= 3) & fin),
                   ("初日×予選系", (DAYIDX == 1) & np.isin(RTYPE, ["予選", "一次予選", "二次予選"]))):
        r = row(base & m, lab)
        if r: print(r)
