import pandas as pd, numpy as np, itertools
import os
D=os.environ.get('PER_PLAN_DIR','./')
e=pd.read_csv(D+'ent.csv'); q=pd.read_csv(D+'q.csv'); p3=pd.read_csv(D+'p3.csv').set_index('race_key')
q['key']=q.combination.astype(str).map(lambda s:tuple(sorted(int(x) for x in s.replace('=','-').split('-'))))
Q={(r,k):o for r,k,o in zip(q.race_key,q.key,q.odds_value)}
rows=[]
for rk,g in e.groupby('race_key'):
    f=g[g.finish_order.isin([1,2])]
    if len(f)!=2 or (g.prediction_mark>0).sum()!=4: continue
    top2=tuple(sorted(f.frame_no)); od=Q.get((rk,top2))
    if od is None or not np.isfinite(od): continue
    m=g[g.prediction_mark>0].sort_values('prediction_mark').frame_no.tolist()
    # 市場の順位: 各車を含む2車複の 1/オッズ の和
    imp={c:sum(1/Q[(rk,k)] for k in [tuple(sorted((c,d))) for d in g.frame_no if d!=c] if (rk,k) in Q and Q[(rk,k)]>0) for c in g.frame_no}
    mk=sorted(imp,key=lambda c:-imp[c])
    ours=[int(x) for x in str(p3.loc[rk,'p3_order']).split('-')] if rk in p3.index and isinstance(p3.loc[rk,'p3_order'],str) else None
    r=dict(race_key=rk,date=g.race_date.iloc[0],n=int(g.n_entries.iloc[0]),odds=od,top2=top2)
    for nm,lst in [('WT',m),('市場',mk),('自社',ours)]:
        if lst is None: continue
        for k in [2,3,4,5]:
            if k>len(lst): continue
            s=set(lst[:k]); hit=set(top2)<=s; npts=k*(k-1)//2
            r[f'{nm}{k}_hit']=hit; r[f'{nm}{k}_ret']=od*100*hit/(npts*100)
    rows.append(r)
x=pd.DataFrame(rows); x.to_pickle(D+'wt_box.pkl')
print('レース数',len(x),'うち自社 p3 あり',x['自社4_hit'].notna().sum(), '期間',x.date.min(),x.date.max())
y=x[x['自社4_hit'].notna()]
for n in [7,9]:
  z=y[y.n==n]; print(f'\n== {n}車  n={len(z)}')
  print('箱   点数 | WT 的中/回収 | 市場 的中/回収 | 自社 的中/回収')
  for k in [2,3,4,5]:
    pts=k*(k-1)//2; s=f'{k}車 {pts:2d}点 |'
    for nm in ['WT','市場','自社']:
        c=f'{nm}{k}_hit'
        s+=f' {z[c].astype(float).mean():.1%} / {z[f"{nm}{k}_ret"].astype(float).mean():.1%} |' if c in z else ' - |'
    print(s)
# WT 公式条件の再現（期間全体・全レース）
print('\n全レース WT 4車BOX的中: %.1f%% 回収 %.1f%%  (n=%d)'%(100*x['WT4_hit'].mean(),100*x['WT4_ret'].mean(),len(x)))
