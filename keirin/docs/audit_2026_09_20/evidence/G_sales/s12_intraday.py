import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',100)
d=common.load2()
print("corr(lead_min, avg_sold_minutes) =", round(d[['lead_min','avg_sold_minutes']].corr().iloc[0,1],3),
      " n=",d[['lead_min','avg_sold_minutes']].dropna().shape[0])
print("avg_sold_minutes - lead_min の中央値:", (d['avg_sold_minutes']-d['lead_min']).median())
d['start_ts']=pd.to_numeric(d['start_at'],errors='coerce')
d['buy_ts']=d['start_ts']-d['avg_sold_minutes']*60
d['big']= (d['payout']>=100000).astype(int)
d['anyhit']=d['hit']
rows=[]
for day,gr in d.groupby('race_date'):
    gr=gr.sort_values('start_ts')
    for _,r in gr.iterrows():
        prior=gr[gr['start_ts'] < r['buy_ts']]
        rows.append({'race_key':r['race_key'],'n_prior':len(prior),
                     'prior_big':prior['big'].sum(),'prior_hit':prior['anyhit'].sum(),
                     'prior_hitrate':prior['anyhit'].mean() if len(prior) else np.nan,
                     'prior_payout':prior['payout'].max() if len(prior) else np.nan})
pr=pd.DataFrame(rows)
d=d.merge(pr,on='race_key',how='left')
print("\n購入時点より前に結果の出ていたレース数:", d['n_prior'].describe().round(2).to_dict())
print("そのうち 10万+的中が既に出ていた商品:", int((d['prior_big']>0).sum()), "/", len(d))
sub=d[d['n_prior']>=3].dropna(subset=['meeting','n_entries']).copy()
sub['rno']=sub['race_no'].astype(float)
print("n(事前3件以上)=",len(sub))
print("\n=== 同日先行の10万+的中の有無別（生） ===")
print(sub.groupby(sub['prior_big']>0).agg(n=('paid','size'),mean=('paid','mean'),med=('paid','median'),sold=('sold_any','mean')).round(2))
X,names=ols.design(sub, cols_num=['rno','prior_big','prior_hitrate','n_prior','is_conf'],
   cats=[('rtg','ippan'),('meeting','day')], fe='race_date')
for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数')]:
    res,r2,n=ols.ols(sub[y],X,names); print(f"\n-- y={yl} n={n} R2={r2:.3f}")
    print(res.loc[['prior_big','prior_hitrate','n_prior']].round(3).to_string())
# 想定払戻も統制
s2=sub[sub['pred_mean_payout'].notna()].copy(); s2['lpmp']=np.log(s2['pred_mean_payout'].astype(float))
X,names=ols.design(s2, cols_num=['rno','lpmp','prior_big','prior_hitrate','n_prior','is_conf'],
   cats=[('rtg','ippan'),('meeting','day')], fe='race_date')
res,r2,n=ols.ols(s2['lpaid'],X,names); print(f"\n-- 想定払戻も統制 y=log1p(有償pt) n={n} R2={r2:.3f}")
print(res.loc[['lpmp','prior_big','prior_hitrate','n_prior']].round(3).to_string())
