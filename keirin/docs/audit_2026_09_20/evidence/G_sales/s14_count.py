import ols, common, pandas as pd, numpy as np
pd.set_option('display.width',250)
g=pd.read_csv('daily_series.csv',parse_dates=['date'])
g['ln']=np.log(g['n_races']); g['lpaid_per']=np.log(g['paid']/g['n_races'])
print("=== 1レースあたり売上 vs その日の商品数（構成とトレンドを統制） ===")
X,n_=ols.design(g,cols_num=['ln','share_final','share_hp','t'])
r,r2,n=ols.ols(g['lpaid_per'],X,n_); print(r.round(3).to_string()); print("R2",round(r2,3),"n",n)
print("\n（ln の係数が 0 なら比例＝共食いなし / 負なら共食い / -1 なら日総額が一定）")
# 8月/9月別
for lab,sub in [('8月(legacy)',g[g['date']<'2026-08-29']),('8/29以降(型ラボ)',g[g['date']>='2026-08-29'])]:
    X,n_=ols.design(sub,cols_num=['ln','share_final','share_hp','t'])
    r,r2,n=ols.ols(sub['lpaid_per'],X,n_); print(f"\n[{lab}] n={n} R2={r2:.3f}"); print(r.loc[['ln']].round(3).to_string())
# 総額版
print("\n=== 日総額 log(paid) vs log(件数) ===")
X,n_=ols.design(g,cols_num=['ln','share_final','share_hp','t'])
r,r2,n=ols.ols(g['lpaid'],X,n_); print(r.round(3).to_string())
# レース単位: その日の件数が増えると1商品の売上は落ちるか（日FEは使えないので構成統制）
d=common.load2()
d=d.merge(g[['date','n_races','share_hp','t']],on='date',how='left')
d['lnr']=np.log(d['n_races']); d['rno']=d['race_no'].astype(float)
dd=d.dropna(subset=['meeting','n_entries'])
X,n_=ols.design(dd,cols_num=['lnr','rno','t','is_conf'],cats=[('rtg','ippan'),('meeting','day')])
r,r2,n=ols.ols(dd['lpaid'],X,n_); print("\n=== レース単位 log1p(有償pt) ~ log(その日の商品数) ===")
print(r.loc[['lnr','t']].round(3).to_string(),"  n=",n,"R2=",round(r2,3))
