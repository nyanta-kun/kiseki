import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
d = common.load2()
d['rk']=d['rank_key'].fillna('NONE')
def pg(r):
    if r.endswith('_sign'): return 'sign(看板枠)'
    if r.endswith('_big'): return 'big(高額枠)'
    if r in ('A_ana','T_upset','A_trio','F_line','F_pay'): return 'ana/pay(穴・高配当)'
    if r.endswith('_hit'): return 'hit(的中重視)'
    if r.startswith('T_'): return 'tier(段)'
    if r[0].isdigit(): return 'legacy(旧ランク)'
    return 'other'
d['pgrp']=d['rk'].apply(pg)
print(d.groupby('pgrp').agg(n=('paid','size'),mean=('paid','mean'),med=('paid','median'),
     sold=('sold_any','mean'),final_share=('rtg',lambda s:(s=='final').mean()),
     conf=('is_conf','mean')).round(3).to_string())

b=d[d['rk']!='NONE'].copy(); b['rno']=b['race_no'].astype(float)
b=b.dropna(subset=['n_entries','meeting'])
print("\n### 商品群 + レースの格を同時に入れる（日FE） ###")
X,names=ols.design(b, cols_num=['rno','n_entries','is_conf'],
  cats=[('rtg','ippan'),('cg','1'),('meeting','day'),('day_index','1'),('pgrp','hit(的中重視)')], fe='race_date')
for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数')]:
    res,r2,n=ols.ols(b[y],X,names); print(f"\n-- y={yl} n={n} R2={r2:.3f}")
    print(res[~res.index.str.startswith('FE_')].round(3).to_string())

print("\n### 点数・想定払戻・リードタイム（型ラボ商品のみ） ###")
t=d[d['n_legs'].notna()].dropna(subset=['n_entries','meeting','lead_min']).copy()
t['rno']=t['race_no'].astype(float); t['lpmp']=np.log(t['pred_mean_payout'].astype(float))
t['lead_h']=t['lead_min']/60.0
print("n=",len(t))
X,names=ols.design(t, cols_num=['rno','n_entries','n_legs','lpmp','lead_h','is_conf'],
  cats=[('rtg','ippan'),('cg','1'),('meeting','day'),('day_index','1')], fe='race_date')
for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数')]:
    res,r2,n=ols.ols(t[y],X,names); print(f"\n-- y={yl} n={n} R2={r2:.3f}")
    print(res[~res.index.str.startswith('FE_')].round(3).to_string())
