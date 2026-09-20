import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
m=pd.read_pickle(f"{OUT}/market_merged.pkl")
def lg(p): p=np.clip(p,1e-6,1-1e-6); return np.log(p/(1-p))
rng=np.random.default_rng(3)
for tag,(a,b,y) in {"win":("mkt_pw","mdl_pw","win_flag"),
                    "top3":("mkt_p3","mdl_p3","top3_flag")}.items():
    d=m.dropna(subset=[a,b]).copy()
    X=np.c_[lg(d[a]),lg(d[b])]; Y=d[y].values
    print(f"\n--- {tag} --- n={len(d)} races={d.race_key.nunique()}")
    print("  corr(logit市場, logitモデル) = %.4f"%np.corrcoef(X[:,0],X[:,1])[0,1])
    f=LogisticRegression(max_iter=2000,C=1e6).fit(X,Y)
    print("  pooled 係数: 市場 %.4f / モデル %.4f  切片 %.4f"%(f.coef_[0][0],f.coef_[0][1],f.intercept_[0]))
    rks=d.race_key.unique(); idx=pd.Series(range(len(rks)),index=rks)
    grp=d.race_key.map(idx).values
    order=np.argsort(grp); Xs,Ys,gs=X[order],Y[order],grp[order]
    starts=np.searchsorted(gs,np.arange(len(rks))); ends=np.r_[starts[1:],len(gs)]
    bs=[]
    for _ in range(300):
        s=rng.integers(0,len(rks),len(rks))
        ii=np.concatenate([np.arange(starts[k],ends[k]) for k in s])
        ff=LogisticRegression(max_iter=2000,C=1e6).fit(Xs[ii],Ys[ii])
        bs.append(ff.coef_[0])
    bs=np.array(bs)
    print("  CI95 市場 [%.4f, %.4f] / モデル [%.4f, %.4f]"%(
        *np.percentile(bs[:,0],[2.5,97.5]), *np.percentile(bs[:,1],[2.5,97.5])))
