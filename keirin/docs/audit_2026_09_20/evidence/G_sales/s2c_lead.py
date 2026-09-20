import common, pandas as pd, numpy as np
pd.set_option('display.width',250)
d = common.load()
d['pub']=pd.to_datetime(d['published_at'],format='mixed',errors='coerce')
d['start']=pd.to_datetime(pd.to_numeric(d['start_at'],errors='coerce'),unit='s',errors='coerce')
# start は UTC 基準のUNIX秒 -> naive UTC。published_at は naive JST か? 確認
print(d[['race_key','published_at','start_at']].head(3))
print("start(UTC naive) sample:", d['start'].head(3).tolist())
d['start_jst']=d['start']+pd.Timedelta(hours=9)
d['lead_min']=(d['start_jst']-d['pub']).dt.total_seconds()/60
print("\nlead_min describe (all non-null):")
print(d['lead_min'].describe().round(1))
print("\n分位:", d['lead_min'].quantile([0,.01,.05,.25,.5,.75,.95,.99,1]).round(1).to_dict())
sel=d[d['lead_min'].between(0,1440)].copy()
print("n in [0,1440]:",len(sel))
sel['lq']=pd.qcut(sel['lead_min'],5,labels=False)
print(sel.groupby('lq').agg(n=('paid','size'),lead=('lead_min','median'),mean=('paid','mean'),
    sold_rate=('nsold',lambda s:(s>0).mean()),avgmin=('avg_sold_minutes','median')).round(2))
sel[['race_key','race_date','lead_min','paid','nsold','rank_key','race_type','cup_grade','day_index','race_no','grade','meeting','first_hour','avg_sold_minutes','is_confident','type_label','plan_key','n_legs','pred_mean_payout','n_entries','start_hour','origin','hit','hit_excl','payout','stake']].to_csv('lead.csv',index=False)
