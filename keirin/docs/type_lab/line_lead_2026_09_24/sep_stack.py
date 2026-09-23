import pandas as pd, numpy as np
# --- 過去2年: legs.pkl(目) + units_occ2(ライン属性)
L=pd.read_pickle("LN/legs.pkl"); U=pd.read_pickle("LN/units_occ2.pkl")
L=L.join(U[["race_key","line_sum","mean_diff"]],on="u")
L=L[~((L.rel=="第三のライン:番手以降")|(L.c_ik==7)|(L.c_rk==7))]
L["period"]=L.win
# --- 9月: 同じ規則で作り直す
R=pd.read_csv("SEP/races.csv"); E=pd.read_csv("SEP/entries.csv"); T=pd.read_csv("SEP/tf.csv")
OD={k:dict(zip(g.combination,g.odds_value)) for k,g in T.groupby("race_key")}
rows=[]
for rk,g in E[E.race_key.isin(set(R[R.n_entries==7].race_key))].groupby("race_key"):
    if len(g)!=7 or g.race_point.isna().any() or g.line_group.isna().any(): continue
    fo=g.set_index("frame_no").finish_order; top=fo[(fo>=1)&(fo<=3)].sort_values()
    if len(top)!=3 or top.duplicated().any(): continue
    res="-".join(str(int(f)) for f in top.index)
    g=g.copy(); g["rk"]=g.race_point.rank(ascending=False,method="min"); g["ik"]=g.pred_top3_pct.rank(ascending=False,method="min")
    rp1=g.loc[g.rk.idxmin()]; sz=g.groupby("line_group").frame_no.size(); ls=g.groupby("line_group").race_point.sum()
    if sz[rp1.line_group]>3: continue
    for lg,h in g.groupby("line_group"):
        if len(h)<2 or lg==rp1.line_group: continue
        h=h.sort_values("line_pos"); a,b=h.iloc[0],h.iloc[1]
        if a["style"]!="逃": continue
        A,B=int(a.frame_no),int(b.frame_no)
        md=ls[rp1.line_group]/sz[rp1.line_group]-ls[lg]/len(h)
        for c in g.itertuples():
            f=int(c.frame_no)
            if f in (A,B): continue
            if (c.line_group!=lg and c.line_group!=rp1.line_group and sz[c.line_group]>=2 and c.line_pos>=2) or c.ik==7 or c.rk==7: continue
            combo=f"{A}-{B}-{f}"
            rows.append(dict(race_key=rk,date=rk[:8],period="9月",lead_rk=a.rk,line_size=len(h),line_sum=ls[lg],mean_diff=md,
                             hit=(combo==res),odds=OD.get(rk,{}).get(res,np.nan)))
S=pd.DataFrame(rows)
A_=pd.concat([L[["race_key","date","period","lead_rk","line_size","line_sum","mean_diff","hit","odds"]],S],ignore_index=True)
rules={"基本（別ラインの先頭が逃）":A_.index==A_.index,
 "③ ＋先頭の得点5位以下":A_.lead_rk>=5,
 "D ③＋2車ライン":(A_.lead_rk>=5)&(A_.line_size==2),
 "E D＋平均得点差>4":(A_.lead_rk>=5)&(A_.line_size==2)&(A_.mean_diff>4),
 "C ③＋ラインの得点合計≤175":(A_.lead_rk>=5)&(A_.line_sum<=175)}
rng=np.random.default_rng(0); out=[]
for nm,m in rules.items():
    q=A_[m].assign(ho=lambda z:z.odds.where(z.hit)); r=q.groupby(["period","race_key","date"]).agg(n=("hit","size"),hit=("hit","max"),odds=("ho","max")).reset_index()
    r["stake"]=(10000/r.n//100*100); r["inv"]=r.stake*r.n; r["ret"]=np.where(r.hit,r.odds*r.stake,0)
    for p in ["探索","確認","9月"]:
        z=r[r.period==p]; d=z.groupby("date")[["inv","ret"]].sum(); v=d.values
        bs=[v[i].sum(0) for i in [rng.integers(0,len(v),len(v)) for _ in range(1000)]]; lo,hi=np.percentile([b[1]/b[0] for b in bs],[2.5,97.5])
        out.append(dict(条件=nm,期間=p,レース日=round(len(z)/len(d),1),点数=round(z.n.mean(),1),的中率=f"{z.hit.mean():.1%}",的中日=round(z.hit.sum()/len(d),2),
            回収率=f"{z.ret.sum()/z.inv.sum():.1%}",CI=f"[{lo:.0%},{hi:.0%}]",上位3除く=f"{(z.ret.sum()-z.ret.nlargest(3).sum())/z.inv.sum():.0%}",
            百超日=f"{(d.ret>=d.inv).mean():.0%}",的中ゼロ日=f"{(d.ret==0).mean():.0%}",最高払戻=int(z.ret.max())))
o=pd.DataFrame(out); pd.set_option("display.width",250); print(o.to_string(index=False))
S.to_pickle("SEP/legs_sep.pkl"); A_.to_pickle("SEP/legs_all.pkl")
