#!/usr/bin/env python3
"""50倍帯のレース種別スキャン（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import PROB, PO, WIN, PAY, DATE, RTYPE, OK, EXP, CNF, POSMASK
from frame import buy

for lo in (50, 30):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, 5)
    print("=" * 132)
    print(f"■ 予測オッズ {lo}倍+ 確率順5点 — レース種別別（壁 74.85%）")
    print("=" * 132)
    print(f"{'種別':<14}{'件/日':>7}{'探索 的中':>10}{'ROI':>8}{'確認 的中':>10}{'ROI':>8}"
          f"{'通算ROI':>9}{'両窓壁超':>8}  中央払戻")
    rows = []
    for rt in sorted(set(RTYPE[OK])):
        m0 = (RTYPE == rt) & OK & (n > 0)
        if m0.sum() < 300:
            continue
        e, c = m0 & EXP, m0 & CNF
        if e.sum() < 100 or c.sum() < 100:
            continue
        re_, rc = ret[e].sum()/inv[e].sum()*100, ret[c].sum()/inv[c].sum()*100
        tot = ret[m0].sum()/inv[m0].sum()*100
        days = len(set(DATE[m0]))
        med = np.median(ret[m0][hit[m0]]) if hit[m0].any() else 0
        rows.append((tot, f"{rt:<14}{m0.sum()/days:>7.2f}{hit[e].mean()*100:>9.2f}%{re_:>7.1f}%"
                          f"{hit[c].mean()*100:>9.2f}%{rc:>7.1f}%{tot:>8.1f}%"
                          f"{'  🟢' if min(re_,rc)>74.85 else '   -':>8}  {med:>9,.0f}円"))
    for _, s in sorted(rows, reverse=True):
        print(s)
    print()
