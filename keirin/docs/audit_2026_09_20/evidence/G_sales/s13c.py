import pandas as pd, numpy as np
pd.set_option('display.width',260); pd.set_option('display.max_rows',100)
r=pd.read_csv('races.csv',dtype={'race_date':str})
s=pd.read_csv('subs.csv'); s['date']=s['race_key'].str[:8]
s['hit']=s['settled_hit'].astype(str).str.lower().isin(['t','true'])
sa=s[(s['status']!='deleted')&(s['settled_at'].notna())]
m=r.merge(sa[['race_key','rank_key','settled_bet','settled_payout','hit','settled_n_combos']],on='race_key',how='inner',suffixes=('','_own'))
w=m[m['race_date']>='20260816'].copy()
w['nk_hit']=w['n_hits_incl_garami']>0
w['d_pay']=w['payout_amount']-w['settled_payout']
w['d_bet']=w['stake_amount']-w['settled_bet']
print("レース単位 n=",len(w))
print("的中(ガミ含)一致:", int((w['nk_hit']==w['hit']).sum()), "/", len(w))
print("払戻一致:", int((w['d_pay']==0).sum()), "/", len(w))
print("賭金一致:", int((w['d_bet']==0).sum()), "/", len(w))
print("\n不一致レース:")
bad=w[(w['nk_hit']!=w['hit'])|(w['d_pay']!=0)]
print(bad[['race_key','rank_key','race_label','stake_amount','payout_amount','n_hits_incl_garami','n_hits_excl_garami','settled_bet','settled_payout','hit','settled_n_combos']].to_string())
