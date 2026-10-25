"""2026-10-21 検証 §1: 売ったレースの中で、売った商品と他プランを同じレースで比べる。

使い方: .venv/bin/python scripts/exp_bet_review/review_20261021_same_race.py PKLDIR 2026-09-08 2026-10-20
（PKLDIR は review_20261021_dump.py の出力先）
"""
import pandas as pd, numpy as np, sys
S=sys.argv[1]; D0,D1=sys.argv[2],sys.argv[3]
L=pd.read_pickle(f"{S}/live.pkl"); Sb=pd.read_pickle(f"{S}/subs.pkl")
L=L[(L.race_date.astype(str)>=D0)&(L.race_date.astype(str)<=D1)&L.settled&(L.plan_key!='T_axis')].copy()
L['payout']=L.payout.fillna(0)+L.void_refund.fillna(0)*0  # void refund treated separately below
s=Sb[(Sb.status=='published')]
sold=s[['race_key','rank_key','origin']].rename(columns={'rank_key':'sold_plan'})
X=L.merge(sold,on='race_key',how='inner')
races=X.groupby('race_key').filter(lambda g:(g.plan_key==g.sold_plan.iloc[0]).any()).copy()
races['day']=races.race_date.astype(str)
sold_rows=races[races.plan_key==races.sold_plan]
print(f"window {D0}..{D1} sold settled races={sold_rows.race_key.nunique()} days={sold_rows.day.nunique()}")
def roi(df): return 100*df.payout.sum()/df.budget.sum()
def ex(df,k):
    p=np.sort(df.payout.values)[::-1]; return 100*(p[k:].sum())/(df.budget.sum()-k*10000)
print(f"SOLD roi={roi(sold_rows):.1f} ex1={ex(sold_rows,1):.1f} ex3={ex(sold_rows,3):.1f} hit={100*sold_rows.hit.mean():.1f}% 10万+={ (sold_rows.payout>=100000).sum()} 4万+={(sold_rows.payout>=40000).sum()}")
print("plans per sold race:", races.groupby('race_key').size().describe()[['mean','min','max']].round(1).to_dict())
# random one plan per race
rng=np.random.default_rng(0); g=races.groupby('race_key')
idx=[np.asarray(v) for v in g.indices.values()]
pay=races.payout.values
rs=[]; 
for _ in range(500):
    pick=np.array([i[rng.integers(len(i))] for i in idx]); rs.append(100*pay[pick].sum()/(10000*len(idx)))
rs=np.array(rs); print(f"RANDOM plan/race: median {np.median(rs):.1f}  [p2.5 {np.percentile(rs,2.5):.1f}, p97.5 {np.percentile(rs,97.5):.1f}]  sold percentile={100*(rs<roi(sold_rows)).mean():.0f}")
best=g.payout.max(); print(f"HINDSIGHT best-of-N: {100*best.sum()/(10000*len(best)):.1f}%  races with any 10万+ row: {(best>=100000).sum()}  (sold caught {(sold_rows.payout>=100000).sum()})")
# fixed swaps: for each sold race, replace with family variant
def fam(p): return p.split('_')[0]
races['fam']=races.sold_plan.map(fam)
alts={'sign':lambda f:f+'_sign','big':lambda f:f+'_big','hit':lambda f:f+'_hit','T_firm':lambda f:'T_firm','T_mid':lambda f:'T_mid','T_upset':lambda f:'T_upset','L_lead':lambda f:'L_lead'}
days=sorted(sold_rows.day.unique())
for name,fn in alts.items():
    r=races[races.plan_key==races.fam.map(fn)][['race_key','day','payout']]
    j=sold_rows[['race_key','day','payout']].merge(r,on=['race_key','day'],suffixes=('_s','_a'))
    if len(j)<50: print(name,'n',len(j)); continue
    dd=j.groupby('day')[['payout_s','payout_a']].agg(['sum','count'])
    ps,pa,n=dd[('payout_s','sum')].values,dd[('payout_a','sum')].values,dd[('payout_s','count')].values
    bs=[]
    for _ in range(2000):
        k=rng.integers(len(n),size=len(n)); bs.append(100*(pa[k].sum()-ps[k].sum())/(10000*n[k].sum()))
    a=j.payout_a.values; aa=np.sort(a)[::-1]
    print(f"swap->{name:8s} n={len(j):4d} sold={100*j.payout_s.sum()/(1e4*len(j)):.1f} alt={100*a.sum()/(1e4*len(j)):.1f} alt_ex1={100*aa[1:].sum()/(1e4*(len(j)-1)):.1f} alt_ex3={100*aa[3:].sum()/(1e4*(len(j)-3)):.1f} d={np.mean(bs):+.1f} CI[{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}] althit={100*(a>0).mean():.1f}% alt10万+={(a>=1e5).sum()}")
