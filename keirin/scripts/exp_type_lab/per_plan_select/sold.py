import pandas as pd, numpy as np
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_pickle(D+'live.pkl'); d['sold']=d.sold.astype(str).isin(['t','True'])
s=d[d.sold]
H=['A_hit','B_hit','C_hit','D_hit','E_hit','F_hit','A_trio','F_line']
s['grp']=np.where(s.plan_key.isin(H),'的中狙い','高額狙い')
days=s.race_date.nunique()
for w,z in [('9/30-11/2',s),('10/26-11/2',s[s.race_date>='2026-10-26'])]:
    g=z.groupby('grp').agg(n=('dhit','size'),hit=('dhit','mean'),sp=('sp','mean'),ret=('ret','sum'),b=('budget','sum'))
    g['roi']=g.ret/g.b; g['per_day']=g.n/z.race_date.nunique()
    print(w,'全体 hit %.3f roi %.3f'%(z.dhit.mean(),z.ret.sum()/z.budget.sum())); print(g[['n','per_day','hit','sp','roi']].round(3))
z=s[s.race_date>='2026-10-26']
print(z.groupby('plan_key').agg(n=('dhit','size'),hit=('dhit','mean'),sp=('sp','mean'),ret=('ret','sum'),b=('budget','sum')).assign(roi=lambda g:g.ret/g.b)[['n','hit','sp','roi']].round(3))
