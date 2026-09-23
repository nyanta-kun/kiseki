import pandas as pd, numpy as np
d=pd.read_pickle("LN/d2.pkl")
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(d.race_key))][["race_key","frame_no","line_group","line_pos","line_size"]]
LP={rk:g for rk,g in fe.groupby("race_key")}
t=lambda a,b,c:f"{a}-{b}-{c}"
def pairs(rk):   # 2車以上のラインの (先頭, 番手)
    g=LP[rk]; out=[]
    for lg,h in g.groupby("line_group"):
        if len(h)>=2:
            h=h.sort_values("line_pos"); out.append((int(h.frame_no.iloc[0]),int(h.frame_no.iloc[1])))
    return out
def legs(x,k):
    cars=list(x.idx); A,B=cars[0],cars[1]
    if k=="G1''": return [t(A,cars[b],cars[c]) for b in (2,3) for c in range(2,7) if c!=b]
    P=pairs(x.race_key)
    if k=="L1 全ラインの先頭→番手→流し": return [t(a,b,c) for a,b in P for c in cars if c not in (a,b)]
    if k=="L2 得点1位のいないライン 先頭→番手→流し": 
        P=[p for p in P if x.rp1 not in p]; return [t(a,b,c) for a,b in P for c in cars if c not in (a,b)]
    if k=="L3 得点1位のライン 先頭→番手→流し":
        P=[p for p in P if x.rp1 in p]; return [t(a,b,c) for a,b in P for c in cars if c not in (a,b)]
top=[[int(v) for v in s.split("-")] for s in d.res]
lf={rk:dict(zip(g.frame_no,g.line_group)) for rk,g in LP.items()}
d["line12"]=[lf[rk].get(t_[0])==lf[rk].get(t_[1]) for rk,t_ in zip(d.race_key,top)]
q=d[d["k_現行の選別"]<=5]
print("1・2着が同じライン（ライン決着）の率: 全レース",f"{d.line12.mean():.1%}"," 波乱候補上位5本",f"{q.line12.mean():.1%}")
print(" 50倍超の決着のうちライン決着:",f"{d[d.big].line12.mean():.1%}")
rng=np.random.default_rng(0)
for k in ["G1''","L1 全ラインの先頭→番手→流し","L2 得点1位のいないライン 先頭→番手→流し","L3 得点1位のライン 先頭→番手→流し"]:
    L=[legs(x,k) for x in d.itertuples()]
    d["st"]=[len(l)*1000 for l in L]; d["rt"]=[x.res_odds*1000 if x.res in l else 0 for x,l in zip(d.itertuples(),L)]
    s=[]
    for w in ["探索","確認"]:
        W=d[(d.win==w)&(d.st>0)]; sel=W[W["k_現行の選別"]<=5]; dd=sel.groupby("race_date")[["st","rt"]].sum()
        cr=[]
        for sd in range(20):
            z=W.assign(u=np.random.default_rng(sd).random(len(W))); z=z[z.u.groupby(z.race_date).rank()<=5]; cr.append(z.rt.sum()/z.st.sum())
        s.append(f"{w} 点{sel.st.mean()/1000:.1f} 的中{(sel.rt>0).mean():.1%} ROI{sel.rt.sum()/sel.st.sum():.1%}(無作為{np.median(cr):.1%}) 百超日{(dd.rt>=dd.st).mean():.1%} | 全レースROI{W.rt.sum()/W.st.sum():.1%}")
    print(f"{k}\n   "+"\n   ".join(s))
