from common import *
d=pd.read_pickle("ds.pkl")
d["head"]=d["title"].fillna("").str.split("｜").str[0]
t=d[(d["date"]>="20260901")&(d["date"]<="20260911")].copy()
t=t[t["plan"].isin(["F_hit","C_hit","B_hit","E_hit","A_hit"])]
t["post"]=(t["date"]>="20260904").astype(float)       # 09/03 は混在なので落とす
t=t[t["date"]!="20260903"]
t["treat"]=(t["plan"]=="F_hit").astype(float)
t["did"]=t["post"]*t["treat"]
print("### DiD: F_hit のタイトルだけが「一撃」→「押さえ」に変わった（09/03）。他プランは不変")
print(t.groupby(["plan","post"]).agg(n=("paid","size"),paid=("paid","mean"),lp=("lpaid","mean"),
     pay=("plan_pay","median"),legs=("n_legs","median")).round(2).to_string())
X,names=dmat(t,["treat","post","did"],["seg"]); res,_,_=ols(t["lpaid"].values,X,names)
print("\n  DiD 係数 (y=log1p有償pt):", {k:tuple(round(x,3) for x in res[k]) for k in ["treat","post","did"]})
X,names=dmat(t,["treat","did"],["date","seg"]); res,_,_=ols(t["lpaid"].values,X,names)
v=res["did"]; print(f"  日FE版 did = {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]  n={len(t)}")
# 生の比: F_hit / 対照の同日平均
ctl=t[t["treat"]==0].groupby("date")["paid"].mean().rename("c")
u=t[t["treat"]>0].join(ctl,on="date"); u["rel"]=u["paid"]/u["c"]
print("\n  F_hit / 同日対照平均:", u.groupby("post")["rel"].agg(["size","mean","median"]).round(3).to_dict())
rng=np.random.default_rng(5)
A=u[u["post"]==0]["rel"].values; B=u[u["post"]>0]["rel"].values
bs=[rng.choice(A,len(A),True).mean()-rng.choice(B,len(B),True).mean() for _ in range(6000)]
print(f"  比の差(前−後) = {A.mean()-B.mean():+.3f} CI95 [{np.percentile(bs,2.5):+.3f},{np.percentile(bs,97.5):+.3f}]  (n前={len(A)}, n後={len(B)})")
