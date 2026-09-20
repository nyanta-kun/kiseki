import common, pandas as pd, numpy as np
pd.set_option('display.width',250)
d=common.load2(); d['rk']=d['rank_key'].fillna('NONE')
b=d[(d['race_date']>='20260906')&(d['type_label'].isin(['B','C','D']))&(d['n_entries']==7)].copy()
b['arm']=np.where(b['rk'].str.endswith(('_sign','_big')),'highpay','normal')
cols=['race_no','start_hour','lead_min','n_legs','pred_mean_payout','axis_sum','gap','avg_sold_minutes','day_index','budget']
print(b.groupby('arm')[cols].median().round(2).to_string())
print()
print(b.groupby('arm')[cols].mean().round(2).to_string())
print("\nmeeting分布:"); print(pd.crosstab(b['arm'],b['meeting'],normalize='index').round(3))
print("\nsession分布:"); print(pd.crosstab(b['arm'],b['session'],normalize='index').round(3))
print("\n的中(ガミ含む)率:"); print(b.groupby('arm')[['hit','hit_excl']].mean().round(3))
print("\n1商品あたり: 売上/点数");
print(b.groupby('arm').agg(paid=('paid','mean'),legs=('n_legs','mean'),pmp=('pred_mean_payout','mean')).round(1))
