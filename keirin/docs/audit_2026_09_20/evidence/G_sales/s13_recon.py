import pandas as pd, numpy as np
pd.set_option('display.width',260); pd.set_option('display.max_rows',100)
dl=pd.read_csv('daily.csv',dtype={'sale_date':str})
r=pd.read_csv('races.csv',dtype={'race_date':str})
s=pd.read_csv('subs.csv')
s['date']=s['race_key'].str[:8]
# 自社: 採点済みの入稿（削除を除く）
sa=s[(s['status']!='deleted')&(s['settled_at'].notna())]
own=sa.groupby('date').agg(own_n=('race_key','size'),own_hit=('settled_hit',lambda x:(x.astype(str).str.lower().isin(['t','true'])).sum()),
    own_bet=('settled_bet','sum'),own_pay=('settled_payout','sum')).reset_index()
own['own_hit_excl']=sa.assign(e=(sa['settled_payout']>sa['settled_bet'])).groupby('date')['e'].sum().values
m=dl.merge(own,left_on='sale_date',right_on='date',how='outer')
m['d_n']=m['n_predictions']-m['own_n']
m['d_hit']=m['n_hits_incl_garami']-m['own_hit']
m['d_hitx']=m['n_hits_excl_garami']-m['own_hit_excl']
m['d_stake']=m['stake_amount']-m['own_bet']
m['d_pay']=m['payout_amount']-m['own_pay']
cols=['sale_date','n_predictions','own_n','d_n','n_hits_incl_garami','own_hit','d_hit',
 'n_hits_excl_garami','own_hit_excl','d_hitx','stake_amount','own_bet','d_stake','payout_amount','own_pay','d_pay']
print(m[cols].to_string())
print("\n完全一致日数:", int(((m['d_n']==0)&(m['d_hit']==0)&(m['d_hitx']==0)&(m['d_stake']==0)&(m['d_pay']==0)).sum()), "/", len(m))
print("件数一致:", int((m['d_n']==0).sum()), " 的中(ガミ含)一致:", int((m['d_hit']==0).sum()),
      " 的中(ガミ除)一致:", int((m['d_hitx']==0).sum()), " 賭金一致:", int((m['d_stake']==0).sum()),
      " 払戻一致:", int((m['d_pay']==0).sum()))
print("\n累計 netkeirin: n=%d hit=%d hitx=%d stake=%d payout=%d" % (m['n_predictions'].sum(),m['n_hits_incl_garami'].sum(),m['n_hits_excl_garami'].sum(),m['stake_amount'].sum(),m['payout_amount'].sum()))
print("累計 自社     : n=%d hit=%d hitx=%d stake=%d payout=%d" % (m['own_n'].sum(),m['own_hit'].sum(),m['own_hit_excl'].sum(),m['own_bet'].sum(),m['own_pay'].sum()))
# daily vs race 集計の突合（自己整合）
rr=r.groupby('race_date').agg(rn=('race_id','size'),rh=('n_hits_incl_garami','sum'),rx=('n_hits_excl_garami','sum'),
   rs=('stake_amount','sum'),rp=('payout_amount','sum'),rpaid=('sold_paid_points','sum')).reset_index()
mm=dl.merge(rr,left_on='sale_date',right_on='race_date')
for a,b,nm in [('n_predictions','rn','件数'),('n_hits_incl_garami','rh','的中'),('n_hits_excl_garami','rx','表示的中'),
               ('stake_amount','rs','賭金'),('payout_amount','rp','払戻'),('sold_paid_points','rpaid','有償pt')]:
    print(f"daily vs race集計 {nm}: 一致 {int((mm[a]==mm[b]).sum())}/{len(mm)}  総差 {int(mm[a].sum()-mm[b].sum())}")
