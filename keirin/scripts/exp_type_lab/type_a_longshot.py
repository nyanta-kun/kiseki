#!/usr/bin/env python3
"""提案A ③: なぜ「飛ぶ側」が壁を超えられないのか（2026-08-31）。

型A の三連複35点すべてを予測オッズ帯に分け、100円ずつ買ったときの回収率を出す。
人気薄ほど回収率が落ちる（favourite-longshot bias）なら、飛ぶ側の商品は
モデルの精度と無関係に壁の下へ張り付く。
"""
import sys
from pathlib import Path, itertools
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, type_a_upset2 as M
data = M.load()
C3 = list(itertools.combinations(range(1, 8), 3))
for win,(lo,hi) in M.WINDOWS.items():
    rs=[d for d in data if lo<=d["date"]<=hi]
    # 型A の全35点を「予測三連複オッズ」帯に分け、各帯を100円ずつ買ったときの回収率
    band=[(0,3),(3,6),(6,12),(12,25),(25,50),(50,100),(100,300),(300,1e9)]
    agg={b:[0,0,0] for b in band}      # [賭け点数, 払戻合計, 的中数]
    for d in rs:
        w=frozenset(d["f"])
        for c in C3:
            fc=frozenset(c)
            po=M._trio_po(d, fc)
            if po<=0: continue
            fo=d["trio_final"].get(fc)
            if fo is None: continue
            for b in band:
                if b[0]<=po<b[1]:
                    agg[b][0]+=100
                    if fc==w: agg[b][1]+=100*fo; agg[b][2]+=1
                    break
    print(f"\n=== {win}  型A {len(rs):,}R・35点すべてを100円ずつ買ったときの帯別回収率 ===")
    print(f"  {'予測三連複オッズ帯':<16}{'点数':>9}{'的中':>7}{'的中率':>8}{'回収率':>9}")
    for b in band:
        n,p,h=agg[b]
        if n<5000: continue
        print(f"  {f'{b[0]:g}〜{b[1]:g}倍':<16}{n//100:>9,}{h:>7,}{h/(n//100):>8.2%}{p/n:>9.1%}")
