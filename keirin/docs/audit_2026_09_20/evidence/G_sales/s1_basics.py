import common, pandas as pd, numpy as np
pd.set_option('display.width',200); pd.set_option('display.max_columns',50)
d = common.load()
print("=== 期間・規模 ===")
print("races:", len(d), "days:", d['date'].nunique(), d['date'].min().date(), "-", d['date'].max().date())
print("total paid pt:", d['paid'].sum(), " total gross pt:", d['sold_points'].sum(),
      " paid/gross:", round(d['paid'].sum()/d['sold_points'].sum(),4))
print("売れた商品(n_sold>0)の割合:", round((d['nsold']>0).mean(),4), f"({(d['nsold']>0).sum()}/{len(d)})")
print("\n=== 1レースあたり売上(有償pt)の分布 ===")
q=[0,.1,.25,.5,.75,.9,.95,.99,1.0]
print(d['paid'].quantile(q))
print("上位10%のレースが占める売上シェア:",
      round(d['paid'].sort_values(ascending=False).head(int(len(d)*0.1)).sum()/d['paid'].sum(),4))
print("上位20%:", round(d['paid'].sort_values(ascending=False).head(int(len(d)*0.2)).sum()/d['paid'].sum(),4))
print("\n=== 日別推移 ===")
g = d.groupby('date').agg(n_races=('paid','size'), paid=('paid','sum'), nsold=('nsold','sum'),
                          hit=('hit','mean'), hit_excl=('hit_excl','mean'),
                          stake=('stake','sum'), payout=('payout','sum'))
g['roi']=g['payout']/g['stake']
g['paid_per_race']=g['paid']/g['n_races']
print(g.to_string())
print("\n=== 週次 ===")
w = d.set_index('date').resample('W')[['paid','nsold']].agg(['sum','size'])
print(w)
print("\n=== 曜日別 ===")
dw = d.groupby('dow').agg(n=('paid','size'), paid_sum=('paid','sum'), paid_mean=('paid','mean'))
dw.index=['月','火','水','木','金','土','日']
print(dw)
