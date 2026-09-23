import pandas as pd, numpy as np
r=pd.read_pickle("races.pkl"); r=r[r.race_date>="2024-10-01"].copy()
mo=pd.read_csv("LN/miss_odds.csv"); mo=mo.set_index(["race_key","combination"]).odds_value
fill=[mo.get((k,s),np.nan) for k,s in zip(r.race_key,r.res)]
r["res_odds"]=r.res_odds.fillna(pd.Series(fill,index=r.index))
print("払戻の欠け: 補完前 1427 → 補完後",r.res_odds.isna().sum(), " / 全",len(r))
r=r[r.res_odds.notna()]
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(r.race_key))][["race_key","frame_no","race_point","line_group","line_pos","line_size"]]
t=lambda a,b,c:f"{a}-{b}-{c}"
rows=[]
for rk,g in fe.groupby("race_key"):
    cars=g.frame_no.astype(int).tolist(); rp1=int(g.sort_values("race_point",ascending=False).frame_no.iloc[0])
    P=[]
    for lg,h in g.groupby("line_group"):
        if len(h)>=2:
            h=h.sort_values("line_pos"); P.append((int(h.frame_no.iloc[0]),int(h.frame_no.iloc[1])))
    L1=[t(a,b,c) for a,b in P for c in cars if c not in (a,b)]
    L2=[t(a,b,c) for a,b in P if rp1 not in (a,b) for c in cars if c not in (a,b)]
    L2r=[t(b,a,c) for a,b in P if rp1 not in (a,b) for c in cars if c not in (a,b)]   # 番手→先頭
    rows.append(dict(race_key=rk,L1=L1,L2=L2,L2r=L2r,npairs=len(P)))
L=pd.DataFrame(rows); d=r.merge(L,on="race_key"); d["win"]=np.where(d.race_date<"2026-01-01","探索","確認")
rng=np.random.default_rng(0)
for k in ["L1","L2","L2r"]:
    d[k+"st"]=d[k].str.len()*1000; d[k+"rt"]=[x.res_odds*1000 if x.res in l else 0 for x,l in zip(d.itertuples(),d[k])]
    for w in ["探索","確認"]:
        q=d[(d.win==w)&(d[k+"st"]>0)]; dd=q.groupby("race_date")[[k+"st",k+"rt"]].sum().values
        bs=[dd[i].sum(0) for i in [rng.integers(0,len(dd),len(dd)) for _ in range(2000)]]; lo,hi=np.percentile([b[1]/b[0] for b in bs],[2.5,97.5])
        print(f"{k:4s} {w} R/日{len(q)/q.race_date.nunique():.1f} 点{q[k+'st'].mean()/1000:.1f} 的中{(q[k+'rt']>0).mean():.1%} 払戻中央{int(q[k+'rt'][q[k+'rt']>0].median()):,} ROI {q[k+'rt'].sum()/q[k+'st'].sum():.1%} [{lo:.1%},{hi:.1%}]  投資/日{int(dd[:,0].mean()):,} 百超日{(dd[:,1]>=dd[:,0]).mean():.1%}")
q=d[d.L2st>0].copy(); q["qt"]=pd.PeriodIndex(q.race_date,freq="Q").astype(str)
g=q.groupby("qt")[["L2st","L2rt"]].sum(); print("\nL2 四半期別ROI:",(g.L2rt/g.L2st*100).round(1).to_dict())
top1=q.groupby("qt").apply(lambda z:(z.L2rt.sum()-z.L2rt.max())/z.L2st.sum()*100,include_groups=False).round(1); print("L2 四半期別 最大1本を除くROI:",top1.to_dict())
d.to_pickle("LN/full.pkl")
