import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
d=common.load2()
t=d[(d['pred_mean_payout'].notna())&(d['race_date']>='20260829')].dropna(subset=['meeting','n_entries']).copy()
t['lpmp']=np.log(t['pred_mean_payout'].astype(float))
t['rno']=t['race_no'].astype(float)
t['q']=pd.qcut(t['pred_mean_payout'],6,labels=False)
print("=== 想定払戻(pred_mean_payout)六分位 ===")
print(t.groupby('q').agg(n=('paid','size'),pmp=('pred_mean_payout','median'),legs=('n_legs','median'),
   paid=('paid','mean'),med=('paid','median'),sold=('sold_any','mean'),hit=('hit','mean')).round(3).to_string())
print("\n=== 日FE+種別+開催種別 で lpmp / n_legs を入れる ===")
X,names=ols.design(t, cols_num=['rno','lpmp','n_legs','is_conf'],
   cats=[('rtg','ippan'),('meeting','day'),('day_index','1')], fe='race_date')
for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数'),('sold_any','P(売れる)')]:
    res,r2,n=ols.ols(t[y],X,names); print(f"\n-- y={yl} n={n} R2={r2:.3f}")
    print(res[~res.index.str.startswith('FE_')].round(3).to_string())
print("\n=== lpmp のみ（n_legs を外す: 強く相関） ===")
print("corr(lpmp, n_legs) =", round(np.corrcoef(t['lpmp'],t['n_legs'].astype(float))[0,1],3))
X,names=ols.design(t, cols_num=['rno','lpmp','is_conf'], cats=[('rtg','ippan'),('meeting','day')], fe='race_date')
res,r2,n=ols.ols(t['lpaid'],X,names); print(res[~res.index.str.startswith('FE_')].round(3).to_string()); print("R2",round(r2,3))
