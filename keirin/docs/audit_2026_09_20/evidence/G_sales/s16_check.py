import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250)
d=common.load2()
print("=== 検証: 売上は結果より前に決まる（プラセボ） ===")
print("raw corr(sold_paid_points, hit) =", round(np.corrcoef(d['paid'],d['hit'])[0,1],3))
print("raw corr(n_sold, hit) =", round(np.corrcoef(d['nsold'],d['hit'])[0,1],3))
t=d[d['pred_mean_payout'].notna()].dropna(subset=['meeting','n_entries']).copy()
t['lpmp']=np.log(t['pred_mean_payout'].astype(float)); t['rno']=t['race_no'].astype(float)
X,n_=ols.design(t,cols_num=['rno','lpmp','is_conf','hit'],cats=[('rtg','ippan'),('meeting','day')],fe='race_date')
r,r2,n=ols.ols(t['lpaid'],X,n_); print("\n想定払戻を統制した上での 当該レースの的中 の係数（0 であるべき）:")
print(r.loc[['hit','lpmp']].round(3).to_string(), " n=",n)
print("\n=== 商品構成の売上シェア（2026-08-29 以降） ===")
e=d[d['race_date']>='20260829'].copy(); e['rk']=e['rank_key'].fillna('NONE')
e['fam']=np.where(e['rk'].str.endswith(('_sign','_big')),'高額枠/看板枠',
        np.where(e['rk'].isin(['A_ana','F_pay','T_upset','A_trio','F_line']),'穴・一撃系','本線/的中系'))
g=e.groupby('fam').agg(n=('paid','size'),paid=('paid','sum'),mean=('paid','mean'),hit=('hit','mean'))
g['n_share']=g['n']/len(e); g['paid_share']=g['paid']/e['paid'].sum()
print(g.round(3).to_string())
print("\n=== 「自信あり」の売上寄与 ===")
c=d[d['is_conf']==1]
print(f"n={len(c)} ({len(c)/len(d):.2%}) 売上={c['paid'].sum():.0f} ({c['paid'].sum()/d['paid'].sum():.2%})")
print("\n=== 決勝の売上寄与 ===")
f=d[d['rtg']=='final']
print(f"n={len(f)} ({len(f)/len(d):.2%}) 売上={f['paid'].sum():.0f} ({f['paid'].sum()/d['paid'].sum():.2%})")
