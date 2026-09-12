#!/usr/bin/env python3
"""(d) 固定構成の腕（◎を1着／◎を2着／◎○を2・3着）を、同じ台で並べる。

比較の相手は本番の一撃商品と同型の `all@T`（=`*_sign`）・`bust@400000`（=`*_big`）。
"""
from __future__ import annotations
import sys
from importlib import import_module
sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
                   "da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/"
                   "08_mark_second_third")
C3 = import_module("03_cmp")
ROWS, NDAYS, WINS = C3.ROWS, C3.NDAYS, C3.WINS
kpi, line, HEAD, boot2, _mk = C3.kpi, C3.line, C3.HEAD, C3.boot2, C3._mk

ARMS = ["d_hon1st_c", "d_hon1st_d", "d_hon2nd_c", "d_hon2nd_d",
        "d_both_c", "d_both_d",
        "all@20000k0", "all@100000k0", "all@150000k0", "bust@400000k0",
        "M@20000k0", "N@100000k12"]
BASE = "all@150000k0"
for lbl, win in WINS:
    rs = [r for r in ROWS if r["win"] == win]
    nd = NDAYS[win]
    print(f"\n{'='*150}\n=== {lbl}  n={len(rs):,}R ===")
    print(HEAD)
    for a in ARMS:
        recs = [r["arms"][a] for r in rs if a in r["arms"]]
        s = kpi(recs, nd)
        if not s:
            print(f"  {a:24s} (該当なし)"); continue
        print(line(a, s))
    print("\n  -- 基準 " + BASE + " との対比較 --")
    for a in ARMS:
        if a == BASE:
            continue
        pairs = [(r["arms"][BASE], r["arms"][a]) for r in rs
                 if BASE in r["arms"] and a in r["arms"]]
        if len(pairs) < 30:
            continue
        res = boot2(_mk([p[0] for p in pairs]), _mk([p[1] for p in pairs]))
        print(f"  {a:24s} " + "  ".join(
            f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]" for k, v in res.items())
            + f"  (n={len(pairs)})")
