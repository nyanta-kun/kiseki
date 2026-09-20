import common, pandas as pd, numpy as np
pd.set_option('display.width',250)
d=common.load2()
print("有償率（有償/総pt）:")
for lab,x in [('全期間',d),('08/17-23(doc の窓)',d[(d['race_date']>='20260817')&(d['race_date']<='20260823')]),
              ('8/29以降',d[d['race_date']>='20260829'])]:
    print(f"  {lab}: {x['paid'].sum()/x['sold_points'].sum():.3f}  無売上率 {(x['nsold']==0).mean():.3f}  件/日 {len(x)/x['date'].nunique():.1f}  個/R {x['nsold'].mean():.2f}")
print("\n10万+的中の商品数:", int((d['payout']>=100000).sum()), "→ 月あたり", round((d['payout']>=100000).sum()/49*30,1))
print("的中1件あたり払戻 中央値（ガミ含む的中のみ）:")
for lab,x in [('8月',d[d['race_date']<'20260901']),('9月',d[d['race_date']>='20260901'])]:
    h=x[x['hit']==1]; print(f"  {lab}: n={len(h)} median={h['payout'].median():.0f} ガミ率={(h['payout']<h['stake']).mean():.3f}")
print("\ndoc §4.2 相対購入指数の再現（同日平均=1.00 に正規化した購入個数）:")
d['rel']=d['nsold']/d.groupby('date')['nsold'].transform('mean')
print(d.groupby('rtg').agg(n=('rel','size'),rel=('rel','mean'),paid_per_R=('paid','mean'),nosale=('nsold',lambda s:(s==0).mean())).round(3).to_string())
print("\ndoc §4.3 時間帯:")
print(d.groupby('meeting').agg(n=('rel','size'),rel=('rel','mean'),paid_per_R=('paid','mean'),nosale=('nsold',lambda s:(s==0).mean())).round(3).to_string())
print("\ndoc §4.4 グレード:")
print(d.groupby('cup_grade').agg(n=('rel','size'),rel=('rel','mean'),paid_per_R=('paid','mean'),nosale=('nsold',lambda s:(s==0).mean())).round(3).to_string())
