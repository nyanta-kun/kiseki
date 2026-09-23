import pandas as pd, numpy as np
d=pd.read_pickle("LN/full.pkl")[["race_key","race_date","win","res","res_odds"]]
ln=pd.read_pickle("LN/d.pkl")[["race_key","comp"]]; d=d.merge(ln,on="race_key",how="left")
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(d.race_key))][["race_key","frame_no","race_point","line_group","line_pos","style"]]
D=d.set_index("race_key"); U=[]
for rk,g in fe.groupby("race_key"):
    if rk not in D.index: continue
    x=D.loc[rk]; g=g.copy(); g["rk"]=g.race_point.rank(ascending=False,method="min")
    rp1=g.loc[g.rk.idxmin()]; sz=g.groupby("line_group").frame_no.size()
    lsum=g.groupby("line_group").race_point.mean().rank(ascending=False,method="min")   # ラインの平均得点順位
    rp1sz=sz[rp1.line_group]; rp1pos="単騎" if rp1sz==1 else ("先頭" if rp1.line_pos==1 else ("番手" if rp1.line_pos==2 else "3番手"))
    n_single=(sz==1).sum(); res=[int(v) for v in x.res.split("-")]
    for lg,h in g.groupby("line_group"):
        if len(h)<2 or lg==rp1.line_group: continue
        h=h.sort_values("line_pos"); a,b=h.iloc[0],h.iloc[1]
        U.append(dict(race_key=rk,win=x.win,date=x.race_date,comp=x.comp,line_size=len(h),rp1_line_size=rp1sz,rp1_pos=rp1pos,
            n_single=n_single,line_rank=lsum[lg],lead_rk=a.rk,ban_rk=b.rk,lead_style=a["style"],
            hit=(res[0]==a.frame_no and res[1]==b.frame_no),odds=x.res_odds))
U=pd.DataFrame(U); U["ret"]=np.where(U.hit,U.odds*1000,0); U["st"]=5000
U.to_pickle("LN/units.pkl"); print("単位数",len(U)," 全体 的中",f"{U.hit.mean():.1%}"," ROI",f"{U.ret.sum()/U.st.sum():.1%}")
def T(by,minn=300):
    g=U.groupby(by+["win"],observed=True).agg(n=("hit","size"),的中=("hit","mean"),ret=("ret","sum"),st=("st","sum"))
    g["ROI"]=(g.ret/g.st*100).round(1); g["的中"]=(g.的中*100).round(1)
    u=g[["n","的中","ROI"]].unstack("win"); return u[(u[("n","探索")]>=minn)&(u[("n","確認")]>=minn*0.4)]
pd.set_option("display.width",250)
for by in [["line_size"],["rp1_line_size","rp1_pos"],["line_size","rp1_line_size"],["n_single"],["comp"],["line_rank"],["lead_rk"],["lead_style"]]:
    print("\n==",by); print(T(by).to_string())
