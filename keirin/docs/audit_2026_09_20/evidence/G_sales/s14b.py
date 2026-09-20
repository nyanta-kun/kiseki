import ols, pandas as pd, numpy as np
pd.set_option('display.width',250)
g=pd.read_csv('daily_series.csv',parse_dates=['date'])
g['ln']=np.log(g['n_races'])
print(g[['date','n_races','paid','nsold']].assign(per=lambda x:(x['paid']/x['n_races']).round(0)).tail(21).to_string())
for lab,sub in [('全期間',g),('8/29以降',g[g['date']>='2026-08-29'])]:
    print(f"\n[{lab}] corr(n_races, paid)=", round(np.corrcoef(sub['n_races'],sub['paid'])[0,1],3),
          " corr(n_races, paid/n)=", round(np.corrcoef(sub['n_races'],sub['paid']/sub['n_races'])[0,1],3),
          " corr(n_races, nsold)=", round(np.corrcoef(sub['n_races'],sub['nsold'])[0,1],3))
# 件数の四分位ごとの日総額（9月）
s=g[g['date']>='2026-08-29'].copy(); s['q']=pd.qcut(s['n_races'],3,labels=['少','中','多'])
print("\n[8/29以降] 商品数3分位:")
print(s.groupby('q',observed=True).agg(n=('paid','size'),races=('n_races','mean'),paid=('paid','mean'),per=('paid',lambda x: 0)).round(1).to_string())
print(s.groupby('q',observed=True).apply(lambda x: pd.Series({'日数':len(x),'平均件数':x['n_races'].mean(),'日売上':x['paid'].mean(),'1件あたり':x['paid'].sum()/x['n_races'].sum()}),include_groups=False).round(1).to_string())
