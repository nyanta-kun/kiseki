from common import *
d=pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
LONG={"A_ana","F_pay","T_upset"}|{f"{x}_{k}" for x in "ABCDEF" for k in ("sign","big")}
t=d[(d["date"]>="20260829")&d["plan_pay"].notna()].copy()
t["conf_f"]=t["conf"].astype(float); t["long_f"]=(t["plan"].isin(LONG)&~t["conf"]).astype(float)
t["lnlegs"]=np.log(t["n_legs"].clip(lower=1))
def run(df,cols,fe,lab,y="lpaid"):
    df=df.dropna(subset=cols+[y])
    X,names=dmat(df,cols,fe); res,b,_=ols(df[y].values,X,names); r=df[y].values-X@b
    print(f"  {lab:<34} n={len(df)} R2={1-r.var()/df[y].var():.4f} " + " ".join(f"{c} {res[c][0]:+.3f}[{res[c][1]:+.2f},{res[c][2]:+.2f}]" for c in cols))
print("### 買い手が実際に見る量（点数）は計画払戻より説明するか")
run(t,["lpay","conf_f"],["date","seg"],"log計画払戻(見えない)")
run(t,["lnlegs","conf_f"],["date","seg"],"log点数(コメントに出る)")
run(t,["long_f","conf_f"],["date","seg"],"穴狙いアイコン(見える)")
run(t,["long_f","lnlegs","conf_f"],["date","seg"],"アイコン+点数")
run(t,["long_f","lnlegs","lpay","conf_f"],["date","seg"],"全部")
print("\n### _sign(15万) vs _big(40万) — 同じアイコン・似た文面・払戻は2.7倍違う")
hp=t[t["origin"]=="highpay_fill"].copy()
hp["kind"]=np.where(hp["plan"].str.endswith("_big"),"_big(40万)","_sign(15万)")
print(hp.groupby("kind").agg(n=("paid","size"),pay=("plan_pay","median"),legs=("n_legs","median"),
   paid=("paid","mean"),med=("paid","median"),sold=("paid",lambda x:(x>0).mean()),
   paidshare=("paid",lambda x:0)).round(1).to_string())
A=hp[hp["kind"]=="_big(40万)"]["paid"].values; B=hp[hp["kind"]=="_sign(15万)"]["paid"].values
rng=np.random.default_rng(3); bs=[rng.choice(A,len(A),True).mean()-rng.choice(B,len(B),True).mean() for _ in range(6000)]
print(f"  差(_big − _sign) = {A.mean()-B.mean():+.0f}pt CI95 [{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}]  (払戻は 2.7倍)")
print("\n### 有償/無償の比率は商品種で違うか（収益は有償ptのみ）")
t["tot"]=t["paid"]+t["free"]
grp=t.groupby(np.where(t["conf"],"自信あり",np.where(t["long_f"]>0,"穴狙い","なし")))
print(grp.apply(lambda s: pd.Series({"n":len(s),"paid":s["paid"].sum(),"free":s["free"].sum(),
   "有償率":s["paid"].sum()/max(s["paid"].sum()+s["free"].sum(),1)})).round(3).to_string())
print("\n### 期別の有償率（無償ptキャンペーンの影響）")
d["tot"]=d["paid"]+d["free"]
per=d.groupby(d["date"].str[:6]).apply(lambda s: pd.Series({"n":len(s),"有償率":s["paid"].sum()/max(s["tot"].sum(),1)}))
print(per.round(3).to_string())
