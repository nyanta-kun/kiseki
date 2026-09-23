import pandas as pd, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
d=pd.read_pickle("LN/d.pkl"); d=d.reset_index(drop=True)
tr=(d.win=="探索").values; te=~tr; y=d.big.values
top=d.race_type.value_counts().index[:8]
def base(df):
    X=df[["asum","pwmax","gap23","same_line","p3ent","pwent","n_lines","rp_sd"]].astype(float).copy()
    for t in top: X["rt_"+t]=(df.race_type==t).astype(float)
    return X
def line(df):
    X=base(df)
    for c in ["2-2-2-1","3-2-2","3-3-1","3-2-1-1","4-3","4-2-1","1-1-1-1-1-1-1","2-2-1-1-1"]: X["c_"+c]=(df.comp==c).astype(float)
    X["same12"]=df.rp12_same.astype(float)
    for p in ["先頭","番手","単騎","3番手以降"]:
        X["p1_"+p]=(df.rp1_pos==p).astype(float); X["p2_"+p]=(df.rp2_pos==p).astype(float)
    X["g12"]=df.rp_gap12.clip(0,15); X["g13"]=df.rp_gap13.clip(0,20); X["g23"]=df.rp_gap23.clip(0,15)
    X["max_line"]=df.max_line; X["rp1_line_rank"]=df.rp1_line_rank
    return X
def only_line(df):
    X=line(df); return X[[c for c in X.columns if c not in base(df).columns or c=="n_lines"]]
res={}
for nm,f in [("現行の選別",base),("ライン・得点だけ",only_line),("現行＋ライン・得点",line)]:
    X=f(d); mu,sd=X[tr].mean(),X[tr].std().replace(0,1); Z=(X-mu)/sd
    m=LogisticRegression(max_iter=3000,C=1.0).fit(Z[tr],y[tr]); s=m.predict_proba(Z)[:,1]
    res[nm]=s; print(f"{nm:14s} AUC(50倍超) 探索 {roc_auc_score(y[tr],s[tr]):.3f} 確認 {roc_auc_score(y[te],s[te]):.3f}   | AUC(100倍超) 確認 {roc_auc_score(d.huge[te],s[te]):.3f}")
    d["k_"+nm]=pd.Series(s).groupby(d.race_date).rank(ascending=False,method="first")
for nm in res:
    for K in [5,10]:
        q=d[d["k_"+nm]<=K]; print(f"  {nm:14s} 日内上位{K:2d}本の50倍超率 探索 {q[q.win=='探索'].big.mean():.1%} 確認 {q[q.win=='確認'].big.mean():.1%}  | 100倍超 {q[q.win=='確認'].huge.mean():.1%}")
d.to_pickle("LN/d2.pkl")
