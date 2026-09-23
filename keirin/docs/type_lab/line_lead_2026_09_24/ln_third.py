import pandas as pd, numpy as np, pickle, glob
U=pd.read_pickle("LN/units_occ2.pkl")
OD=pickle.load(open("odds.pkl","rb")); mo=pd.read_csv("LN/miss_odds.csv")
for rk,g in mo.groupby("race_key"): OD[rk]=dict(zip(g.combination,g.odds_value))
fl=pd.concat([pd.read_csv(f) for f in glob.glob("LN/fill_*.csv")])
for rk,g in fl.groupby("race_key"): OD[rk]={**OD.get(rk,{}),**dict(zip(g.combination,g.odds_value))}
res=pd.read_pickle("LN/full.pkl").set_index("race_key").res
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(U.race_key))][["race_key","frame_no","line_group","line_pos","race_point","style"]]
wf=pd.concat([pd.read_pickle(f) for f in glob.glob("H1/wf70_*.pkl")])[["race_key","frame_no","pp3"]]
fe=fe.merge(wf,on=["race_key","frame_no"],how="left"); LP={k:g for k,g in fe.groupby("race_key")}
L=[]
for x in U.itertuples():
    g=LP[x.race_key].copy(); od=OD.get(x.race_key,{}); r_=res[x.race_key]
    g["rk"]=g.race_point.rank(ascending=False,method="min"); g["ik"]=g.pp3.rank(ascending=False,method="min")
    rp1=g.loc[g.rk.idxmin()]; sz=g.groupby("line_group").frame_no.size()
    for lg,h in g.groupby("line_group"):
        if len(h)!=x.line_size or abs(h.race_point.sum()-x.line_sum)>1e-6: continue
        h=h.sort_values("line_pos"); a,b=int(h.frame_no.iloc[0]),int(h.frame_no.iloc[1])
        for c in g.itertuples():
            f=int(c.frame_no)
            if f in (a,b): continue
            if c.line_group==lg: rel="同ラインの3番手以降"
            elif c.line_group==rp1.line_group: rel="得点1位のライン:"+("得点1位本人" if f==int(rp1.frame_no) else ("先頭" if c.line_pos==1 else "番手以降"))
            elif sz[c.line_group]==1: rel="単騎"
            else: rel="第三のライン:"+("先頭" if c.line_pos==1 else "番手以降")
            o=od.get(f"{a}-{b}-{f}",np.nan)
            L.append(dict(u=x.Index,win=x.win,date=x.date,rel=rel,c_rk=c.rk,c_ik=c.ik,c_style=c.style,odds=o,
                          hit=(r_==f"{a}-{b}-{f}"),lead_rk=x.lead_rk,line_size=x.line_size))
        break
L=pd.DataFrame(L); L["imp"]=0.75/L.odds; L["ret"]=np.where(L.hit,L.odds*1000,0)
L["band"]=pd.cut(L.odds,[0,25,50,100,300,1e9],labels=["〜25","25-50","50-100","100-300","300〜"])
L.to_pickle("LN/legs.pkl"); print("目の数",len(L),"（ユニット",L.u.nunique(),"）")
def T(col):
    g=L.groupby([col,"win"],observed=True).agg(n=("hit","size"),的中=("hit","mean"),h=("hit","sum"),imp=("imp","sum"),ret=("ret","sum"))
    g["発生倍率"]=(g.h/g.imp).round(2); g["ROI"]=(g.ret/(g.n*1000)*100).round(1); g["的中"]=(g.的中*100).round(2)
    return g[["n","的中","発生倍率","ROI"]].unstack("win")
pd.set_option("display.width",250)
for c,lab in [("rel","3着の車とラインの関係"),("c_rk","3着の車の競走得点順位"),("c_ik","3着の車の指数(3着内率)順位"),("c_style","3着の車の脚質"),("band","その目のオッズ帯")]:
    print(f"\n== {lab} =="); print(T(c).to_string())
