import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
d=common.load2(); d['rk']=d['rank_key'].fillna('NONE')
def pg(r):
    if r.endswith('_sign'): return 'sign'
    if r.endswith('_big'): return 'big'
    if r in ('A_ana','T_upset','A_trio','F_line','F_pay'): return 'ana_pay'
    if r.endswith('_hit'): return 'hit'
    if r.startswith('T_'): return 'tier'
    if r[0].isdigit(): return 'legacy'
    return 'other'
d['pgrp']=d['rk'].apply(pg)
b=d[(d['race_date']>='20260829')&(d['pgrp'].isin(['hit','ana_pay','sign','big']))].dropna(subset=['n_entries','meeting']).copy()
b['rno']=b['race_no'].astype(float)
print("n=",len(b), b.groupby('pgrp').size().to_dict())
print("\n生の平均:")
print(b.groupby('pgrp').agg(n=('paid','size'),mean=('paid','mean'),med=('paid','median'),sold=('sold_any','mean'),conf=('is_conf','mean')).round(3))
X,names=ols.design(b, cols_num=['rno','n_entries','is_conf'],
  cats=[('rtg','ippan'),('cg','1'),('meeting','day'),('day_index','1'),('pgrp','hit')], fe='race_date')
for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数'),('sold_any','P(売れる)')]:
    res,r2,n=ols.ols(b[y],X,names); print(f"\n-- y={yl} n={n} R2={r2:.3f}")
    print(res[~res.index.str.startswith('FE_')].round(3).to_string())

print("\n\n=== is_confident の詳細（35件） ===")
c=d[d['is_conf']==1]
print(c.groupby('race_date').agg(n=('paid','size'),mean=('paid','mean')).to_string())
print(c[['race_key','race_date','rank_key','rtg','paid','nsold','cup_grade','race_type']].to_string())
