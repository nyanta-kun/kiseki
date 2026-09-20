import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
d=common.load2(); d['rk']=d['rank_key'].fillna('NONE')
def pg(r):
    if r.endswith('_sign'): return 'sign'
    if r.endswith('_big'): return 'big'
    if r in ('A_ana','T_upset','A_trio','F_line','F_pay'): return 'ana_pay'
    if r.endswith('_hit'): return 'hit'
    if r.startswith('T_'): return 'tier'
    if r[0].isdigit(): return 'legacy'
    return 'other'
d['pgrp']=d['rk'].apply(pg)
e2=d[(d['race_date']>='20260901')].copy()   # sign 導入後
w=e2[e2['race_type'].isin(['決勝','チャレンジ決勝','準決勝','チャレンジ準決勝'])]
print("=== 決勝/準決勝レースのみ: 商品群別 ===")
print(w.groupby('pgrp').agg(n=('paid','size'),mean=('paid','mean'),med=('paid','median'),
      sold=('sold_any','mean'),nsold=('nsold','mean')).round(2).to_string())
print("\n種別×商品群:")
print(w.pivot_table(index='race_type',columns='pgrp',values='paid',aggfunc=['size','mean']).round(1).to_string())
# ブートストラップ差
def boot(a,b,n=20000,seed=0):
    rng=np.random.default_rng(seed); a=np.asarray(a); b=np.asarray(b)
    dif=[rng.choice(a,len(a)).mean()-rng.choice(b,len(b)).mean() for _ in range(n)]
    return np.mean(a)-np.mean(b), np.percentile(dif,[2.5,97.5])
for rt in ['決勝','準決勝']:
    s=w[(w['race_type'].str.contains(rt))&(~w['race_type'].str.contains('準決勝') if rt=='決勝' else True)]
    A=s[s['pgrp']=='sign']['paid'].values; B=s[s['pgrp']=='hit']['paid'].values
    if len(A)>3 and len(B)>3:
        dm,ci=boot(A,B); print(f"\n{rt}: sign n={len(A)} mean={A.mean():.0f} / hit n={len(B)} mean={B.mean():.0f} 差={dm:.0f} CI95=[{ci[0]:.0f},{ci[1]:.0f}]")

print("\n\n=== is_confident: 同日・同種別のペア比較 ===")
c=d[d['is_conf']==1].copy()
rows=[]
for _,r in c.iterrows():
    peers=d[(d['race_date']==r['race_date'])&(d['is_conf']==0)&(d['rtg']==r['rtg'])]
    if len(peers)>=2:
        rows.append({'date':r['race_date'],'rtg':r['rtg'],'conf_paid':r['paid'],'peer_med':peers['paid'].median(),'peer_mean':peers['paid'].mean(),'n_peer':len(peers)})
p=pd.DataFrame(rows)
print(p.round(1).to_string())
print("\nn=",len(p),"conf平均",p['conf_paid'].mean().round(1),"同日同種別peer平均",p['peer_mean'].mean().round(1))
diff=p['conf_paid']-p['peer_mean']
rng=np.random.default_rng(1); bs=[rng.choice(diff.values,len(diff)).mean() for _ in range(20000)]
print("差の平均",diff.mean().round(1),"CI95",np.percentile(bs,[2.5,97.5]).round(1))
print("勝ち(conf>peer平均)の件数:", (diff>0).sum(),"/",len(diff))
