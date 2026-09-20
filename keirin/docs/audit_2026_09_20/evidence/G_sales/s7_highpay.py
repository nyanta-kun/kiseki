import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
d=common.load2(); d['rk']=d['rank_key'].fillna('NONE')
# B/C/D 型のみ: _hit(通常) vs _sign/_big(高額枠=本来捨てるレース)
b=d[(d['race_date']>='20260906')&(d['type_label'].isin(['B','C','D']))&(d['n_entries']==7)].copy()
b['arm']=np.where(b['rk'].str.endswith(('_sign','_big')),'highpay','normal')
print("n=",len(b))
print(b.groupby(['type_label','arm']).agg(n=('paid','size'),mean=('paid','mean'),med=('paid','median'),
     sold=('sold_any','mean'),nsold=('nsold','mean'),final=('rtg',lambda s:(s=='final').mean()),
     semi=('rtg',lambda s:(s=='semifinal').mean())).round(3).to_string())
def boot(a,b_,n=20000,seed=0):
    rng=np.random.default_rng(seed); a=np.asarray(a,float); b_=np.asarray(b_,float)
    dif=[rng.choice(a,len(a)).mean()-rng.choice(b_,len(b_)).mean() for _ in range(n)]
    return a.mean()-b_.mean(), np.percentile(dif,[2.5,97.5])
A=b[b['arm']=='highpay']['paid'].values; B=b[b['arm']=='normal']['paid'].values
dm,ci=boot(A,B); print(f"\n全体 highpay n={len(A)} mean={A.mean():.0f} vs normal n={len(B)} mean={B.mean():.0f}: 差={dm:.0f} CI95=[{ci[0]:.0f},{ci[1]:.0f}]")
# 同じ種別グループ内で
for g in ['final','semifinal','tokusen','yosen','ippan']:
    s=b[b['rtg']==g]; A=s[s['arm']=='highpay']['paid'].values; B=s[s['arm']=='normal']['paid'].values
    if len(A)>=5 and len(B)>=5:
        dm,ci=boot(A,B); print(f"{g}: hp n={len(A)} {A.mean():.0f} / norm n={len(B)} {B.mean():.0f} 差={dm:.0f} CI=[{ci[0]:.0f},{ci[1]:.0f}]")
# 回帰（日FE + 種別）
bb=b.dropna(subset=['meeting'])
X,names=ols.design(bb, cols_num=['is_conf'], cats=[('rtg','ippan'),('arm','normal'),('meeting','day')], fe='race_date')
for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数')]:
    res,r2,n=ols.ols(bb[y],X,names); print(f"\n-- y={yl} n={n} R2={r2:.3f}")
    print(res[~res.index.str.startswith('FE_')].round(3).to_string())
