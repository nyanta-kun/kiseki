import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
d=common.load2()
d['big_payout']=(d['payout']>=100000).astype(float)
g=d.groupby('date').agg(
  n_races=('paid','size'), paid=('paid','sum'), nsold=('nsold','sum'),
  hit=('hit','mean'), hit_excl=('hit_excl','mean'),
  stake=('stake','sum'), payout=('payout','sum'), maxpay=('payout','max'),
  n_big=('big_payout','sum'),
  share_final=('rtg',lambda s:(s=='final').mean()),
  mean_lpmp=('pred_mean_payout',lambda s: np.log(s.dropna()).mean() if s.notna().any() else np.nan),
  share_hp=('rank_key',lambda s: s.fillna('').str.endswith(('_sign','_big')).mean()),
).reset_index()
g['roi']=g['payout']/g['stake']
g['t']=np.arange(len(g))
g['lpaid']=np.log(g['paid'])
g['lmaxpay']=np.log1p(g['maxpay'])
print(g.round(3).to_string())
g.to_csv('daily_series.csv',index=False)
