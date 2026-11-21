import pandas as pd, numpy as np, sys, os
os.chdir(os.path.dirname(__file__))
df=pd.read_csv('subs.csv'); df=df[df.d.str.match(r'^\d{8}$')].copy(); df['d']=df.d.astype(int)
for c in ['bet','pay','nc']: df[c]=df[c].astype(float)
df=df[df.d>=20260925].copy()
df['hit']=(df.pay>df.bet).astype(int); df['ret']=df.pay/df.bet
df['grp']=np.where(df.rank_key.str.contains('sign|ana|pay|upset'),'high',np.where(df.rank_key=='L_lead','L_lead','hitp'))
print(df.groupby('grp').agg(n=('hit','size'),hit=('hit','mean'),roi=('ret','mean'),sd=('ret','std')).round(3))
day=df.groupby('d').agg(n=('hit','size'),hit=('hit','mean'),roi=('ret','mean'))
print('days',len(day),'daily hit mean/sd',day.hit.mean().round(3),day.hit.std().round(3),'roi mean/sd',day.roi.mean().round(3),day.roi.std().round(3))
p=df.hit.mean(); print('binomial sd at n_mean', np.sqrt(p*(1-p)/day.n.mean()).round(3))
for g in ['hitp','high','L_lead']:
    s=df[df.grp==g]; print(g,'per-bet ret sd',round(s.ret.std(),2),'bets/day',round(len(s)/df.d.nunique(),1))
x=df[df.grp!='L_lead']; dd=x.groupby('d').agg(hit=('hit','mean'),roi=('ret','mean'))
print('ex L_lead: daily hit',dd.hit.mean().round(3),dd.hit.std().round(3),'roi',dd.roi.mean().round(3),dd.roi.std().round(3))
x=x.assign(wk=pd.to_datetime(x.d.astype(str)).dt.to_period('W'))
print(x.groupby(['wk','grp']).agg(n=('hit','size'),hit=('hit','mean'),roi=('ret','mean')).round(3).unstack())
