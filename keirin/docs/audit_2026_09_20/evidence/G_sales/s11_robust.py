import ols, pandas as pd, numpy as np
pd.set_option('display.width',250)
g=pd.read_csv('daily_series.csv', parse_dates=['date'])
g['ln']=np.log(g['n_races'])
g['big_L1']=g['n_big'].shift(1)
g['big_F1']=g['n_big'].shift(-1)     # プラセボ: 翌日の高額的中
g['lpaid_L1']=g['lpaid'].shift(1)
g['roi_L1']=(g['payout'].shift(1))/(g['stake'].shift(1))
g['hitx_L1']=g['hit_excl'].shift(1)
base=['ln','share_final','share_hp','t']
def fit(extra,df=None,label=''):
    d=(df if df is not None else g).dropna(subset=base+extra+['lpaid'])
    X,n_=ols.design(d,cols_num=base+extra); r,r2,n=ols.ols(d['lpaid'],X,n_)
    print(f"\n[{label}] n={n} R2={r2:.3f}"); print(r.loc[extra].round(3).to_string())
    return r
print("高額的中(10万+)があった日:", g.loc[g['n_big']>0,['date','n_big','maxpay','paid']].to_string())
fit(['big_L1'],label='big_L1 単独')
fit(['big_L1','lpaid_L1'],label='big_L1 + 前日売上')
fit(['big_F1'],label='プラセボ: 翌日のbig (未来)')
fit(['big_L1','big_F1'],label='big_L1 と 翌日big 同時')
# 09-08 を落とす
g2=g[g['date']!='2026-09-08']
fit(['big_L1'],df=g2,label='big_L1 (09-08を除外)')
g3=g[~g['date'].isin(pd.to_datetime(['2026-09-08','2026-09-01']))]
fit(['big_L1'],df=g3,label='big_L1 (09-08,09-01を除外)')
fit(['roi_L1'],df=g2,label='roi_L1 (09-08除外)')
# 個別: big のあった翌日 vs その他 の残差
d=g.dropna(subset=base+['lpaid']).copy()
X,nn=ols.design(d,cols_num=base); r,_,_=ols.ols(d['lpaid'],X,nn)
d['resid']=d['lpaid']-X@r['coef'].values
d['bigy']=d['n_big'].shift(1).fillna(0)
print("\n=== ベースライン残差: 前日に10万+的中があったか別 ===")
print(d.groupby(d['bigy']>0)['resid'].agg(['count','mean','median','std']).round(3))
print(d.loc[d['bigy']>0,['date','bigy','resid','paid']].round(3).to_string())
rng=np.random.default_rng(0)
a=d.loc[d['bigy']>0,'resid'].values; b=d.loc[d['bigy']==0,'resid'].values
bs=[rng.choice(a,len(a)).mean()-rng.choice(b,len(b)).mean() for _ in range(20000)]
print("差(log) =",round(a.mean()-b.mean(),3),"CI95",np.round(np.percentile(bs,[2.5,97.5]),3),
      " → 倍率",round(np.exp(a.mean()-b.mean()),2))
