import pandas as pd, numpy as np, glob
from sklearn.linear_model import LogisticRegression
d=pd.read_pickle("LN/full.pkl"); d=d[d.L2st>0].reset_index(drop=True)
wf=pd.concat([pd.read_pickle(f) for f in glob.glob("H1/wf70_*.pkl")])[["race_key","frame_no","pp3","ppw"]]
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(d.race_key))][["race_key","frame_no","race_point","line_group","race_type"]]
x=fe.merge(wf,on=["race_key","frame_no"])
F=[]
for rk,g in x.groupby("race_key"):
    if len(g)!=7: continue
    g=g.sort_values("pp3",ascending=False); p3=g.pp3.values; pw=g.ppw.values
    a=p3/p3.sum(); b=pw/pw.sum()
    F.append(dict(race_key=rk,asum=p3[0]+p3[1],pwmax=pw.max(),gap23=p3[1]-p3[2],same_line=float(g.line_group.iloc[0]==g.line_group.iloc[1]),
      p3ent=-(a*np.log(a)).sum(),pwent=-(b*np.log(b+1e-12)).sum(),n_lines=g.line_group.nunique(),rp_sd=g.race_point.std(ddof=0),rtype=g.race_type.iloc[0]))
F=pd.DataFrame(F); d=d.merge(F,on="race_key"); print("レース数",len(d))
d["big"]=d.res_odds>=50; top=d.rtype.value_counts().index[:8]
X=d[["asum","pwmax","gap23","same_line","p3ent","pwent","n_lines","rp_sd"]].astype(float)
for t in top: X["rt_"+t]=(d.rtype==t).astype(float)
tr=(d.win=="探索").values; mu,sd=X[tr].mean(),X[tr].std().replace(0,1)
m=LogisticRegression(max_iter=3000).fit((X[tr]-mu)/sd,d.big[tr]); d["us"]=m.predict_proba((X-mu)/sd)[:,1]
d["k"]=d.us.groupby(d.race_date).rank(ascending=False,method="first")
rng=np.random.default_rng(0)
for K in [5,8]:
  for w in ["探索","確認"]:
    W=d[d.win==w]; s=W[W.k<=K]; dd=s.groupby("race_date")[["L2st","L2rt"]].sum(); v=dd.values
    bs=[v[i].sum(0) for i in [rng.integers(0,len(v),len(v)) for _ in range(2000)]]; lo,hi=np.percentile([b[1]/b[0] for b in bs],[2.5,97.5])
    cr=[];cp=[]
    for sd_ in range(20):
        z=W.assign(u=np.random.default_rng(sd_).random(len(W))); z=z[z.u.groupby(z.race_date).rank()<=K]; zz=z.groupby("race_date")[["L2st","L2rt"]].sum()
        cr.append(zz.L2rt.sum()/zz.L2st.sum()); cp.append((zz.L2rt>=zz.L2st).mean())
    roi=s.L2rt.sum()/s.L2st.sum(); p=(dd.L2rt>=dd.L2st).mean(); pl=dd.L2rt-dd.L2st; cum=pl.cumsum()
    m_=s.assign(mo=s.race_date.str[:7]).groupby("mo")[["L2st","L2rt"]].sum()
    print(f"K={K} {w}: 投資{int(dd.L2st.mean()):,}/日 点{s.L2st.mean()/1000:.1f} 的中{(s.L2rt>0).mean():.1%} 払戻中央{int(s.L2rt[s.L2rt>0].median()):,} ROI {roi:.1%} [{lo:.1%},{hi:.1%}] (無作為 {np.median(cr):.1%} 勝{sum(roi>c for c in cr)}/20)  百超日 {p:.1%} (無作為 {np.median(cp):.1%} 勝{sum(p>c for c in cp)}/20)  百超月 {(m_.L2rt>=m_.L2st).sum()}/{len(m_)} 最大DD {int((cum-cum.cummax()).min()):,}")
q=d[d.k<=8].copy(); q["qt"]=pd.PeriodIndex(q.race_date,freq="Q").astype(str)
g=q.groupby("qt")[["L2st","L2rt"]].sum(); print("K=8 四半期別ROI:",(g.L2rt/g.L2st*100).round(1).to_dict())
d.to_pickle("LN/final.pkl")
