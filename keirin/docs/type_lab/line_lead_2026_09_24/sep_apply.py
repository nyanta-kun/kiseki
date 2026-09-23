import pandas as pd, numpy as np
R=pd.read_csv("SEP/races.csv"); E=pd.read_csv("SEP/entries.csv"); T=pd.read_csv("SEP/tf.csv")
print("style値:",E["style"].value_counts().to_dict(), " 指数欠け率",round(E.pred_top3_pct.isna().mean(),3))
OD={k:dict(zip(g.combination,g.odds_value)) for k,g in T.groupby("race_key")}
R7=set(R[R.n_entries==7].race_key)
rows=[];legrows=[]
for rk,g in E[E.race_key.isin(R7)].groupby("race_key"):
    if len(g)!=7 or g.race_point.isna().any() or g.line_group.isna().any(): continue
    fo=g.set_index("frame_no").finish_order
    top=fo[(fo>=1)&(fo<=3)].sort_values()
    settled=len(top)==3 and not top.duplicated().any()
    res="-".join(str(int(f)) for f in top.index) if settled else None
    g=g.copy(); g["rk"]=g.race_point.rank(ascending=False,method="min"); g["ik"]=g.pred_top3_pct.rank(ascending=False,method="min")
    rp1=g.loc[g.rk.idxmin()]; sz=g.groupby("line_group").frame_no.size()
    if sz[rp1.line_group]>3: continue
    for lg,h in g.groupby("line_group"):
        if len(h)<2 or lg==rp1.line_group: continue
        h=h.sort_values("line_pos"); a,b=h.iloc[0],h.iloc[1]
        if a["style"]!="逃": continue
        A,B=int(a.frame_no),int(b.frame_no); legs5=[];legs3=[]
        for c in g.itertuples():
            f=int(c.frame_no)
            if f in (A,B): continue
            combo=f"{A}-{B}-{f}"; legs5.append(combo)
            third_back=(c.line_group!=lg and c.line_group!=rp1.line_group and sz[c.line_group]>=2 and c.line_pos>=2)
            if third_back or c.ik==7 or c.rk==7: continue
            legs3.append(combo)
        od=OD.get(rk,{}); ro=od.get(res) if res else None
        rows.append(dict(race_key=rk,day=rk[:8],settled=settled,res=res,odds=ro,n5=len(legs5),n3=len(legs3),
            hit5=bool(res in legs5) if res else False, hit3=bool(res in legs3) if res else False, lead_rk=a.rk))
U=pd.DataFrame(rows); U=U[U.settled]
print("払戻オッズ欠け（確定済み）:",U.odds.isna().sum())
U["ret3"]=np.where(U.hit3,U.odds*1000,0); U["ret5"]=np.where(U.hit5,U.odds*1000,0)
d=U.groupby("day").agg(レース=("race_key","nunique"),ライン=("race_key","size"),点数=("n3","sum"),的中=("hit3","sum"),払戻=("ret3","sum"),最高払戻=("ret3","max"),
                       点数5=("n5","sum"),的中5=("hit5","sum"),払戻5=("ret5","sum"))
d["投資"]=d.点数*1000; d["回収率"]=(d.払戻/d.投資*100).round(1); d["回収率(3着総流し)"]=(d.払戻5/(d.点数5*1000)*100).round(1)
out=d[["レース","ライン","点数","投資","的中","払戻","回収率","最高払戻","的中5","回収率(3着総流し)"]].copy()
out.index=[f"{i[4:6]}/{i[6:]}" for i in out.index]
tot=out.sum(numeric_only=True); 
pd.set_option("display.width",220)
print(out.astype({"払戻":int,"最高払戻":int}).to_string())
print(f"\n合計: レース{int(tot.レース)} ライン{int(tot.ライン)} 点数{int(tot.点数)} 投資{int(tot.投資):,} 的中{int(tot.的中)} 払戻{int(tot.払戻):,} 回収率{tot.払戻/tot.投資*100:.1f}%  最高払戻{int(out.最高払戻.max()):,}")
print(f"3着総流し: 的中{int(tot.的中5)} 回収率{U.ret5.sum()/(U.n5.sum()*1000)*100:.1f}%")
print("100%超えの日:",(out.回収率>=100).sum(),"/",len(out)," 最大3本を除く回収率",f"{(U.ret3.sum()-U.ret3.nlargest(3).sum())/(U.n3.sum()*1000)*100:.1f}%")
print("\n的中一覧（払戻上位10）:"); print(U[U.hit3].sort_values("ret3",ascending=False).head(10)[["race_key","res","odds","ret3","lead_rk"]].to_string(index=False))
U.to_pickle("SEP/units.pkl")
