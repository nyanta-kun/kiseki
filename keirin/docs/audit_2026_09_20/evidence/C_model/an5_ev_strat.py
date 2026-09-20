import numpy as np, pandas as pd
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
j=pd.read_pickle(f"{OUT}/ev_joined.pkl")
j["ret"]=j.hit*j.odds_value
print("全目 equal-weight 回収率:", round(j.ret.mean(),4), " n=",len(j))
bands=[(1,10),(10,30),(30,100),(100,300),(300,1000),(1000,1e9)]
rng=np.random.default_rng(11)
def bootci(d,B=2000):
    rks=d.race_key.unique(); idx={k:i for i,k in enumerate(rks)}
    g=d.race_key.map(idx).values
    s=np.bincount(g,weights=d.ret.values,minlength=len(rks)); c=np.bincount(g,minlength=len(rks))
    out=[]
    for _ in range(B):
        k=rng.integers(0,len(rks),len(rks)); out.append(s[k].sum()/max(c[k].sum(),1))
    return np.percentile(out,2.5),np.percentile(out,97.5)
print("\n=== オッズ帯ごと × その帯内でのモデルEV 3分位（市場に対する相対評価）===")
rows=[]
for lo,hi in bands:
    d=j[(j.odds_value>=lo)&(j.odds_value<hi)].copy()
    if len(d)<3000: continue
    d["t"]=pd.qcut(d.ev,3,labels=["低","中","高"],duplicates="drop")
    for t,g in d.groupby("t",observed=True):
        lo95,hi95=bootci(g)
        rows.append(dict(band=f"{lo}-{hi if hi<1e8 else '+'}",ev3=t,n=len(g),
                         ev=g.ev.mean(),odds_med=g.odds_value.median(),
                         hit=g.hit.mean(),ret=g.ret.mean(),ci_lo=lo95,ci_hi=hi95))
print(pd.DataFrame(rows).to_string(index=False,float_format=lambda x:f"{x:.4f}"))
print("\n=== 帯全体（=市場そのまま買う）===")
for lo,hi in bands:
    d=j[(j.odds_value>=lo)&(j.odds_value<hi)]
    if len(d)<3000: continue
    print(f"  {lo}-{hi if hi<1e8 else '+'}: n={len(d):,} 回収率={d.ret.mean():.4f} 的中={d.hit.mean():.5f}")
