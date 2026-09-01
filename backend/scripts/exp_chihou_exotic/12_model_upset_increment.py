"""指数側の波乱度は市場の波乱度に上乗せするか（レース選別の学習化の素地）"""
import pandas as pd, numpy as np
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
band=pd.read_pickle(SP+"band.pkl")
wf=pd.read_csv(SP+"wf_nomarket.csv")
wf=wf[wf.win_prob_wf.notna()]
wf["p"]=wf.win_prob_wf/wf.groupby("race_id")["win_prob_wf"].transform("sum")
ent_p=wf.groupby("race_id").apply(lambda g: -(g.p*np.log(g.p.clip(1e-9))).sum()/np.log(len(g)), include_groups=False).rename("ent_p")
top3p=wf.groupby("race_id")["p"].apply(lambda s: s.nlargest(3).sum()).rename("top3_p")
d=band.join(ent_p, on="race_id").join(top3p, on="race_id").dropna(subset=["ent_p"])
print("n=",len(d), " corr(市場ent, 指数ent)=%.3f"%d.ent.corr(d.ent_p))
def roi(s,th=200):
    inv=100*s[f"n{th}"].sum(); return s.tri[s[f"hit{th}"]==1].sum()/inv if inv>0 else np.nan
d["entq"]=pd.qcut(d.ent,5,labels=[1,2,3,4,5])
d["entpq"]=pd.qcut(d.ent_p,5,labels=[1,2,3,4,5])
print("\n=== 200倍+ ROI: 指数側の波乱度五分位（単独） ===")
print(pd.DataFrame({"ROI":[roi(s) for _,s in d.groupby("entpq",observed=True)],
                    "R":[len(s) for _,s in d.groupby("entpq",observed=True)]},index=[1,2,3,4,5]).round(3).to_string())
print("\n=== 200倍+ ROI: 市場波乱度 × 指数波乱度（5x5→3x3に集約） ===")
d["e3"]=pd.qcut(d.ent,3,labels=["市場堅","中","市場荒"]); d["p3"]=pd.qcut(d.ent_p,3,labels=["指数堅","中","指数荒"])
piv=d.pivot_table(index="e3",columns="p3",values="tri",aggfunc=lambda x:np.nan,observed=True)
out=pd.DataFrame(index=["市場堅","中","市場荒"],columns=["指数堅","中","指数荒"],dtype=float)
cnt=out.copy()
for (a,b),s in d.groupby(["e3","p3"],observed=True):
    out.loc[a,b]=roi(s); cnt.loc[a,b]=len(s)
print(out.round(3).to_string()); print(cnt.astype(int).to_string())
# 残差（指数の波乱度のうち市場で説明できない部分）
co=np.polyfit(d.ent,d.ent_p,1); d["resid"]=d.ent_p-np.polyval(co,d.ent)
d["rq"]=pd.qcut(d.resid,5,labels=[1,2,3,4,5])
print("\n=== 200倍+ ROI: 残差（市場で説明できない指数の荒れ）五分位 ===")
print(pd.DataFrame({"ROI":[roi(s) for _,s in d.groupby("rq",observed=True)],
                    "R":[len(s) for _,s in d.groupby("rq",observed=True)]},index=[1,2,3,4,5]).round(3).to_string())
