#!/usr/bin/env python3
"""提案B ①: 順当側を三連複へ逃がす — オッズ差は本当に大きいのか（2026-08-31）。

同じ3車の「確定三連単 ÷ 確定三連複」の比を帯別に測り、点数別の三連複を
本番ゲートつきで比べる。
"""
import sys, itertools, random
from pathlib import Path
from statistics import median
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import type_a_upset2 as M

data = M.load()

# ── 追加の腕（三連複を点数別に）──
def mk_trio(n):
    def f(d):
        a,b=d["o"][0],d["o"][1]
        return "trio", [frozenset((a,b,d["o"][k])) for k in range(2,2+n)]
    return f
for n in (1,2,3,4):
    M.ARMS[f"三連複 軸2車流し{n}点"] = mk_trio(n)

def mk_tf(n):
    def f(d):
        a,b=d["o"][0],d["o"][1]
        return "tf", [(a,b,d["o"][k]) for k in range(2,2+n)]
    return f
for n in (1,2,3):
    M.ARMS[f"三連単 A_hit形{n}点"] = mk_tf(n)

for win,(lo,hi) in M.WINDOWS.items():
    rs=[d for d in data if lo<=d["date"]<=hi]
    nd=len({d["date"] for d in rs})
    print(f"\n{'='*118}\n=== {win}  型A {len(rs):,}R / {nd}日 ===")

    # ① オッズ差は本当に大きいのか（同じ3車の 確定三連単 ÷ 確定三連複）
    rat=[]; tfo=[]; tro=[]
    for d in rs:
        w=frozenset(d["f"]); fo=d["trio_final"].get(w)
        if fo and fo>0 and d["PAY"]>0:
            tfo.append(d["PAY"]/100.0); tro.append(fo); rat.append((d["PAY"]/100.0)/fo)
    rat.sort(); tfo.sort(); tro.sort()
    print(f"  ① 決着した3車の 確定三連単 ÷ 確定三連複（n={len(rat):,}）")
    print(f"     中央 {median(rat):.2f}倍  四分位 {rat[len(rat)//4]:.2f}〜{rat[len(rat)*3//4]:.2f}"
          f"   （順序6通りが等確率なら 6.0倍）")
    print(f"     三連単 中央 {median(tfo):.1f}倍 ↔ 三連複 中央 {median(tro):.1f}倍")
    for lab,lo2,hi2 in (("<10倍",0,10),("10-30倍",10,30),("30倍+",30,1e9)):
        sub=[r for r,t in zip(rat,tfo) if lo2<=t<hi2]
        sub2=[t for t in tfo if lo2<=t<hi2]
        if sub: print(f"     三連単{lab:<9} n={len(sub):>5,}  比 中央 {median(sub):.2f}倍")

    # ② 点数別の腕（本番ゲートつき）
    print(f"\n  ② 点数別（入稿ゲートを腕ごとに掛け直す・配分ダッチ）")
    print(M.HDR)
    for name in ("三連単 A_hit形1点","三連単 A_hit形2点","A_hit 現行3点",
                 "三連複 軸2車流し1点","三連複 軸2車流し2点","三連複 軸2車流し3点","三連複 軸2車流し4点"):
        recs=[r for r in (M.play(d,name) for d in rs) if r]
        print(M.row(name, M.summ(recs, nd)))

    # ③ 同じ母集団での直接対決（三連複3点が通ったレースだけ）
    ok=[d for d in rs if M.play(d,"三連複 軸2車流し3点")]
    if ok:
        nd2=len({d["date"] for d in ok})
        print(f"\n  ③ 三連複3点が入稿ゲートを通ったレースだけで直接対決（{len(ok):,}R / {nd2}日）")
        print(M.HDR)
        for name in ("A_hit 現行3点","三連複 軸2車流し3点","三連複 軸2車流し4点"):
            recs=[r for r in (M.play(d,name) for d in ok) if r]
            print(M.row(name, M.summ(recs, nd)))
        # 無作為対照（同数）
        rois=[]
        for sd in range(20):
            pick=random.Random(sd).sample(rs,len(ok))
            recs=[r for r in (M.play(d,"A_hit 現行3点") for d in pick) if r]
            s=M.summ(recs,nd,ci=False)
            if s: rois.append(s["roi"])
        rois.sort()
        real=M.summ([r for r in (M.play(d,"A_hit 現行3点") for d in ok) if r],nd,ci=False)
        print(f"     ※ この母集団は A_hit にとって有利か: 無作為対照 中央 {rois[10]:.1f}% "
              f"[{rois[0]:.1f},{rois[-1]:.1f}] ↔ この母集団 {real['roi']:.1f}%")
