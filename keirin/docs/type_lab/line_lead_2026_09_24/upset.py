import pandas as pd, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
S="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-dev-keirin/215ebb0d-e9b5-401f-a659-538e08cabeb6/scratchpad/ax/"
r=pd.read_pickle(S+"races2.pkl")
r["big"]=(r.res_odds>=50).astype(int)
r["p3ent"]=[-(np.array(p)/sum(p)*np.log(np.array(p)/sum(p))).sum() for p in r.p3]
r["pwent"]=[-(np.array(p)/sum(p)*np.log(np.array(p)/sum(p)+1e-12)).sum() for p in r.pw]
r["n_lines"]=[len(set(v for v in x.line.values())) for x in r.itertuples()]
r["mk_ent"]=[-(np.array(m)*np.log(np.array(m)+1e-12)).sum() for m in r.mkt]
r["mk_top2"]=[sum(sorted(m)[-2:]) for m in r.mkt]
r["rp_sd"]=[np.std(p) for p in r.rp]
rt=pd.get_dummies(r.race_type.where(r.race_type.isin(r.race_type.value_counts().index[:8]),"他"),prefix="rt").astype(float)
FM=["asum","pwmax","gap23","same_line","p3ent","pwent","n_lines","rp_sd"]
FK=FM+["mk_top","mk_ent","mk_top2"]
X=pd.concat([r[FK].astype(float),rt],axis=1)
tr=r.win=="探索"; te=~tr
for name,cols in [("モデルだけ(朝に分かる)",FM+list(rt.columns)),("＋市場(確定オッズで代用)",FK+list(rt.columns)),("市場だけ",["mk_top","mk_ent","mk_top2"])]:
    m=LogisticRegression(max_iter=2000).fit((X.loc[tr,cols]-X.loc[tr,cols].mean())/X.loc[tr,cols].std(),r.big[tr])
    s=m.predict_proba((X[cols]-X.loc[tr,cols].mean())/X.loc[tr,cols].std())[:,1]
    r["us_"+name]=s
    print(f"{name}: AUC 探索={roc_auc_score(r.big[tr],s[tr]):.3f} 確認={roc_auc_score(r.big[te],s[te]):.3f}")
    # 日内上位K本の波乱率
    for K in [5,10,15]:
        rk=r.assign(s=s).groupby("race_date").s.rank(ascending=False,method="first")
        sel=rk<=K
        print(f"   日内上位{K:2d}本  50倍+率 探索={r.big[tr&sel].mean():.1%} 確認={r.big[te&sel].mean():.1%} (全体 {r.big.mean():.1%})")
r.to_pickle(S+"races3.pkl")
