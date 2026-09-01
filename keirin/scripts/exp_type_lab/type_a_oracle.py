#!/usr/bin/env python3
"""① 検出が完璧だったときの上限 ② 三連複がなぜ売れないか（2026-08-31）。

`type_a_upset2.py` の台と腕をそのまま使い、到達可能な波乱 T だけを買った場合
（＝どんな検出器も超えられない上限）と、三連複が入稿ゲートのどこで落ちるかを出す。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_oracle.py
"""
import sys, importlib.util, itertools, json, math, re, random
from pathlib import Path
from statistics import median
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import type_a_upset2 as M

data = M.load()
for win,(lo,hi) in M.WINDOWS.items():
    rs=[d for d in data if lo<=d["date"]<=hi]
    nd=len({d["date"] for d in rs})
    T=[d for d in rs if d["o"][0] in d["f"] and d["o"][1] in d["f"] and d["odds"]>=30]
    print(f"\n=== {win}  型A {len(rs):,}R  到達可能な波乱 T {len(T):,}R ({len(T)/len(rs):.1%}) ===")
    print("  ▼ 上限（オラクル）: T を完璧に選べたとして、その中だけ買う")
    print(M.HDR)
    for name in ("A_hit 現行3点","A_pay 6点","軸2車+相手2車 6順列(12点)",
                 "軸2車+相手3車 6順列(18点)","三連複 軸2車流し5点","確率上位10点"):
        recs=[r for r in (M.play(d,name) for d in T) if r]
        print(M.row(name, M.summ(recs, nd)))
    # 三連複がゲートのどこで落ちるか
    n_no=n_pt=n_mean=n_ok=0; means=[]
    for d in rs:
        kind,combos=M.ARMS["三連複 軸2車流し5点"](d)
        combos=list(dict.fromkeys(combos))
        po=[M._trio_po(d,c) for c in combos]
        if any(x<=0 for x in po): n_no+=1; continue
        if min(po)<M.MIN_POINT_ODDS: n_pt+=1; continue
        w=[1.0/x for x in po]; nu=M.BUDGET//M.UNIT; u=[1]*len(combos)
        rest=nu-len(combos); tot=sum(w)
        for j,x in enumerate(w): u[j]+=int(rest*x/tot)
        while sum(u)<nu:
            j=min(range(len(u)),key=lambda k:u[k]/max(w[k],1e-12)); u[j]+=1
        mp=sum(uu*M.UNIT*oo for uu,oo in zip(u,po))/len(combos)
        means.append(mp)
        if mp<=M.MIN_MEAN_PAYOUT: n_mean+=1
        else: n_ok+=1
    print(f"  ▼ 三連複 軸2車流し5点 がどこで落ちるか（{len(rs):,}R）")
    print(f"    予測なし {n_no} / 1点でも2.0倍未満 {n_pt} ({n_pt/len(rs):.1%}) / "
          f"平均想定払戻<=2万円 {n_mean} ({n_mean/len(rs):.1%}) / 通過 {n_ok}")
    if means: print(f"    平均想定払戻の中央 {median(means):,.0f}円")
