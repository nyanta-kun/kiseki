import pandas as pd, numpy as np
pd.set_option('display.width',260)
dl=pd.read_csv('daily.csv',dtype={'sale_date':str})
s=pd.read_csv('subs.csv'); s['date']=s['race_key'].str[:8]
s['hit']=s['settled_hit'].astype(str).str.lower().isin(['t','true'])
sa=s[(s['status']!='deleted')&(s['settled_at'].notna())]
own=sa.groupby('date').agg(own_n=('race_key','size'),own_hit=('hit','sum'),own_bet=('settled_bet','sum'),own_pay=('settled_payout','sum'))
own['own_hitx']=sa.assign(e=sa['settled_payout']>sa['settled_bet']).groupby('date')['e'].sum()
m=dl.merge(own,left_on='sale_date',right_index=True)
w=m[(m['sale_date']>='20260816')]
print("窓 2026-08-16〜09-18 (%d日)"%len(w))
for a,b,nm in [('n_predictions','own_n','件数'),('n_hits_incl_garami','own_hit','的中(ガミ含)'),
               ('n_hits_excl_garami','own_hitx','表示的中'),('stake_amount','own_bet','賭金'),('payout_amount','own_pay','払戻')]:
    d=w[a]-w[b]; print(f"{nm}: 一致 {int((d==0).sum())}/{len(w)}  総差 {int(d.sum())}  (netkeirin計 {int(w[a].sum())} / 自社計 {int(w[b].sum())})")
print("\n食い違う日:")
w2=w.assign(dn=w['n_predictions']-w['own_n'],dh=w['n_hits_incl_garami']-w['own_hit'],
            dx=w['n_hits_excl_garami']-w['own_hitx'],ds=w['stake_amount']-w['own_bet'],dp=w['payout_amount']-w['own_pay'])
print(w2.loc[(w2[['dn','dh','dx','ds','dp']]!=0).any(axis=1),['sale_date','dn','dh','dx','ds','dp']].to_string())
# 内訳: 賭金が 10000 でない入稿
ss=sa[(sa['date']>='20260816')]
print("\n自社 settled_bet の分布:", ss['settled_bet'].value_counts().head().to_dict())
print("10000 でない件:"); print(ss.loc[ss['settled_bet']!=10000,['race_key','rank_key','settled_bet','settled_payout','status']].to_string())
