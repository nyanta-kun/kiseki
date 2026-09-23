import pandas as pd, numpy as np
d=pd.read_pickle("LN/full.pkl")[["race_key","race_date","win","res","res_odds"]]
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(d.race_key))][["race_key","frame_no","race_point","line_group","line_pos","style"]]
D=d.set_index("race_key"); U=[]
for rk,g in fe.groupby("race_key"):
    if rk not in D.index: continue
    x=D.loc[rk]; g=g.copy(); g["rk"]=g.race_point.rank(ascending=False,method="min")
    rp1=g.loc[g.rk.idxmin()]; sz=g.groupby("line_group").frame_no.size(); lsum=g.groupby("line_group").race_point.sum()
    lead=g[g.line_pos==1].set_index("line_group")
    rp1sz=sz[rp1.line_group]; rp1pos="単騎" if rp1sz==1 else ("先頭" if rp1.line_pos==1 else ("番手" if rp1.line_pos==2 else "3番手"))
    rp1_lead_style=lead.style.get(rp1.line_group) if rp1sz>1 else rp1["style"]
    n_nige=(g["style"]=="逃").sum(); n_nige_lead=((g.line_pos==1)&(g["style"]=="逃")).sum()
    n_multi=(sz>=2).sum(); res=[int(v) for v in x.res.split("-")]
    for lg,h in g.groupby("line_group"):
        if len(h)<2 or lg==rp1.line_group: continue
        h=h.sort_values("line_pos"); a,b=h.iloc[0],h.iloc[1]
        if a["style"]!="逃" or rp1sz>3: continue            # ②の母集団
        U.append(dict(race_key=rk,win=x.win,date=x.race_date,line_size=len(h),lead_rk=a.rk,
            line_sum=lsum[lg],rp1_sum=lsum[rp1.line_group],sum_diff=lsum[rp1.line_group]-lsum[lg],
            mean_diff=lsum[rp1.line_group]/rp1sz-lsum[lg]/len(h),
            rp1_pos=rp1pos,rp1_lead_nige=(rp1_lead_style=="逃"),n_nige=n_nige,n_nige_lead=n_nige_lead,
            n_multi=n_multi,n_lines=len(sz),ban_rk=b.rk,
            hit=(res[0]==a.frame_no and res[1]==b.frame_no),odds=x.res_odds))
U=pd.DataFrame(U); U["ret"]=np.where(U.hit,U.odds*1000,0); U["st"]=5000; U["R3"]=U.lead_rk>=5
U.to_pickle("LN/units4.pkl"); print("②の単位数",len(U))
e=U[U.win=="探索"]
def q3(c): 
    b=e[c].quantile([1/3,2/3]).values; return pd.cut(U[c],[-1e9,b[0],b[1],1e9],labels=[f"低(≤{b[0]:.0f})",f"中",f"高(>{b[1]:.0f})"])
U["sum_diff_b"]=q3("sum_diff"); U["mean_diff_b"]=q3("mean_diff"); U["line_sum_b"]=q3("line_sum")
def T(col,sub=None,minn=150):
    Z=U if sub is None else U[sub]
    g=Z.groupby([col,"win"],observed=True).agg(n=("hit","size"),的中=("hit","mean"),ret=("ret","sum"),st=("st","sum"))
    g["ROI"]=(g.ret/g.st*100).round(1); g["的中"]=(g.的中*100).round(1)
    u=g[["n","的中","ROI"]].unstack("win"); return u[(u[("n","探索")]>=minn)]
pd.set_option("display.width",250)
labels={"sum_diff_b":"得点1位ラインの合計 − このラインの合計","mean_diff_b":"平均得点の差（1位ライン − このライン）","line_sum_b":"このラインの得点合計",
 "rp1_pos":"得点1位のライン内の位置","rp1_lead_nige":"得点1位のラインの先頭が逃","n_nige_lead":"先頭が逃のラインの数（レース全体）",
 "n_nige":"逃の選手数（レース全体）","n_multi":"2車以上のラインの数","n_lines":"ライン数（単騎含む）","line_size":"このラインの車数"}
for c,lab in labels.items():
    a=T(c); b=T(c,U.R3,80)
    print(f"\n== {lab} ==  [左: ②全体 / 右: ③(先頭の得点5位以下)]")
    print(a.join(b,rsuffix="③",how="left").to_string())
