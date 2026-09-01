#!/usr/bin/env python3
"""提案A ①: 軸1が飛んだレースの中身に構造があるか（2026-08-31・ユーザー提案）。

型A で「軸1（p3 1位）が3着に入らない」とき、残り6車から新しい軸を選べるか。
一様（6車から3車＝20通り）と比べて、決着がどれだけ偏っているかを両窓で測る。
"""
import sys, itertools
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import type_a_upset2 as M

data = M.load()
W = M.WINDOWS
for win,(lo,hi) in W.items():
    rs=[d for d in data if lo<=d["date"]<=hi]
    bust=[d for d in rs if d["o"][0] not in d["f"]]
    print(f"\n{'='*100}\n=== {win}  型A {len(rs):,}R / 軸1が3着外（軸崩壊）{len(bust):,}R ({len(bust)/len(rs):.1%}) ===")
    md=sorted(d["odds"] for d in bust)
    print(f"  軸崩壊時の確定三連単オッズ 中央 {md[len(md)//2]:.0f}倍 / 100倍+ "
          f"{sum(1 for x in md if x>=100)/len(md):.1%} / 300倍+ {sum(1 for x in md if x>=300)/len(md):.1%}")

    # ① 残り6車の p3 順位が着に絡む率（一様なら 3/6 = 50%）
    print(f"\n  ① 軸崩壊時に「その車が3着内」の率（残り6車・一様なら 50.0%）")
    print(f"     {'p3順位':<8}" + "".join(f"{f'{k+2}位':>8}" for k in range(6)))
    line=f"     {'率':<8}"
    for k in range(1,7):
        line += f"{sum(1 for d in bust if d['o'][k] in d['f'])/len(bust):>8.1%}"
    print(line)

    # ② 新しい軸2車（p3 2位・3位）がそろう率
    n2=sum(1 for d in bust if d["o"][1] in d["f"] and d["o"][2] in d["f"])
    # 一様（6車から3車）なら P(特定2車が両方) = C(4,1)/C(6,3) = 4/20 = 20%
    print(f"\n  ② 新軸候補がそろう率（6車から3車の一様なら 20.0%）")
    print(f"     p3 2位+3位 : {n2/len(bust):>6.1%}")
    for a,b in ((1,3),(2,3),(1,4),(2,4),(3,4)):
        c=sum(1 for d in bust if d["o"][a] in d["f"] and d["o"][b] in d["f"])
        print(f"     p3 {a+1}位+{b+1}位 : {c/len(bust):>6.1%}")

    # ③ 実際の3着組（20通り）の集中度
    cnt=defaultdict(int)
    for d in bust:
        rk=tuple(sorted(d["o"].index(c) for c in d["f"]))   # 0-based p3 順位
        cnt[rk]+=1
    tot=sum(cnt.values())
    top=sorted(cnt.items(), key=lambda kv:-kv[1])
    print(f"\n  ③ 決着の p3 順位の組（軸崩壊時・全 {len(cnt)} 通り・一様なら各 5.0%）")
    for rk,c in top[:6]:
        print(f"     {'-'.join(str(x+1) for x in rk):<10} {c/tot:>6.1%} (n={c})")
    cum=0; need=0
    for rk,c in top:
        cum+=c/tot; need+=1
        if cum>=0.5: break
    print(f"     上位 {need} 通りで 50% / 上位3通りで {sum(c for _,c in top[:3])/tot:.1%} "
          f"（一様なら上位3通りで 15.0%）")

    # ④ モデル確率（軸1を除いて再正規化）で並べたときの上位カバー率
    C3=list(itertools.combinations(range(7),3))
    covs=defaultdict(int)
    for d in bust:
        cars=d["o"][1:]                                  # 残り6車
        pr={}
        for c in itertools.combinations(sorted(cars),3):
            s=sum(float(d["PROB"][M.CIDX[p]]) for p in itertools.permutations(c))
            pr[frozenset(c)]=s
        order=sorted(pr, key=lambda k:-pr[k])
        w=frozenset(d["f"])
        if w in pr:
            r=order.index(w)
            for k in (1,3,5,8,20):
                if r<k: covs[k]+=1
    print(f"\n  ④ 軸1を除いた20通りをモデル確率順に並べたときのカバー率（軸崩壊 {len(bust):,}R）")
    for k in (1,3,5,8,20):
        print(f"     上位{k:>2}点: {covs[k]/len(bust):>6.1%}  (一様なら {k/20:.1%})")
