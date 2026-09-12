#!/usr/bin/env python3
"""本当の「市場 vs モデル」: ◎（または予測オッズ最安）が pw 1位と食い違う稀な層での直接対決。"""
import pickle, numpy as np
D="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/10_axis_swap"
ROWS = pickle.load(open(f"{D}/table.pkl","rb"))
rng = np.random.default_rng(1)
def ci(d,B=2000):
    d=np.asarray(d,float); n=len(d); bs=np.sort([d[rng.integers(0,n,n)].mean() for _ in range(B)])*100
    return d.mean()*100, bs[int(B*.025)], bs[int(B*.975)]
def a2(r,a1): return next(c for c in r["p3_o"] if c!=a1)
for lbl,w in [("探索","explore"),("確認","confirm")]:
    rs=[r for r in ROWS if r["win"]==w]
    for nm, mk in [("◎", lambda r:r["hon"]), ("予測オッズ最安", lambda r:int(np.argmax(r["mk_win"]))+1)]:
        sub=[r for r in rs if mk(r) and mk(r)!=r["pw_o"][0]]
        n=len(sub)
        t=lambda r:set(r["fin"])
        pw1=[r["pw_o"][0] for r in sub]; m=[mk(r) for r in sub]
        w_pw=[r["fin"][0]==a for r,a in zip(sub,pw1)]; w_m=[r["fin"][0]==a for r,a in zip(sub,m)]
        t_pw=[a in t(r) for r,a in zip(sub,pw1)]; t_m=[a in t(r) for r,a in zip(sub,m)]
        s_pw=[{a,a2(r,a)}<=t(r) for r,a in zip(sub,pw1)]; s_m=[{a,a2(r,a)}<=t(r) for r,a in zip(sub,m)]
        mrank=np.mean([r["pw_o"].index(a)+1 for r,a in zip(sub,m)])
        print(f"[{lbl}] {nm} ≠ pw1位: n={n} ({n/len(rs)*100:.1f}%)  {nm}の pw 順位 平均 {mrank:.2f}")
        for k,(x,y) in {"1着率":(w_pw,w_m),"3着内率":(t_pw,t_m),"二軸そろい(軸1=それ/軸2=p3)":(s_pw,s_m)}.items():
            m_,lo,hi=ci(np.array(y,float)-np.array(x,float))
            print(f"   {k:28s} pw1位 {np.mean(x)*100:6.2f}%  {nm} {np.mean(y)*100:6.2f}%  Δ {m_:+.2f} [{lo:+.2f},{hi:+.2f}]")
