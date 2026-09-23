import pandas as pd, numpy as np
L=pd.read_pickle("LN/legs.pkl"); rng=np.random.default_rng(0)
bad3=L.rel=="第三のライン:番手以降"
rules={"T0 流し5点（現行②）":L.index==L.index,
 "T1 第三のライン番手以降を外す":~bad3,
 "T2 T1＋指数7位を外す":~bad3&(L.c_ik<7),
 "T3 T2＋得点7位を外す":~bad3&(L.c_ik<7)&(L.c_rk<7)}
for nm,m in rules.items():
    o=[]
    for w in ["探索","確認"]:
        q=L[m&(L.win==w)]; u=q.groupby(["date","u"]).agg(h=("hit","sum"),r=("ret","sum"),n=("hit","size"),imp=("imp","sum")).reset_index()
        dd=u.groupby("date")[["r","n","h","imp"]].sum(); v=dd[["r","n"]].values
        bs=[v[i].sum(0) for i in [rng.integers(0,len(v),len(v)) for _ in range(1000)]]; lo,hi=np.percentile([b[0]/(b[1]*1000) for b in bs],[2.5,97.5])
        roi=u.r.sum()/(u.n.sum()*1000); top3=(u.r.sum()-u.r.nlargest(3).sum())/(u.n.sum()*1000)
        o.append(f"{w} 点{u.n.mean():.1f} 的中{(u.h>0).mean():.1%} 発生倍率{u.h.sum()/u.imp.sum():.2f} ROI{roi:.1%}[{lo:.0%},{hi:.0%}] 上位3本除く{top3:.0%}")
    print(f"{nm:24s}"," | ".join(o))
# 無作為に同数の3着を外した対照（T2と同じ点数）
for w in ["探索","確認"]:
    q=L[L.win==w]; k=L[(~bad3)&(L.c_ik<7)&(L.win==w)].groupby("u").size()
    rs=[]
    for s in range(20):
        z=q.assign(v=np.random.default_rng(s).random(len(q))); z["rk"]=z.v.groupby(z.u).rank(method="first"); z=z[z.rk<=z.u.map(k).fillna(0)]
        rs.append(z.ret.sum()/(len(z)*1000))
    print(f"  対照（同じ点数を無作為に残す）{w}: ROI中央 {np.median(rs):.1%}  範囲 {min(rs):.1%}〜{max(rs):.1%}")
