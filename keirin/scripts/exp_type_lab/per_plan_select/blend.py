import pandas as pd, numpy as np
import os
D=os.environ.get('PER_PLAN_DIR','./')
e=pd.read_csv(D+'ent.csv'); p3=pd.read_csv(D+'p3.csv').set_index('race_key')
R=[]
for rk,g in e.groupby('race_key'):
    if rk not in p3.index or not isinstance(p3.loc[rk,'p3_order'],str): continue
    if g.finish_order.isna().all() or (g.prediction_mark>0).sum()!=4: continue
    o=[int(x) for x in p3.loc[rk,'p3_order'].split('-')]
    if len(o)!=len(g): continue
    ro={c:i+1 for i,c in enumerate(o)}
    for _,r in g.iterrows():
        R.append(dict(race_key=rk,date=r.race_date,car=r.frame_no,ours=ro[r.frame_no],wt=r.prediction_mark if r.prediction_mark>0 else 5.5,fin=r.finish_order))
x=pd.DataFrame(R); x['top3']=x.fin.between(1,3); x['top2']=x.fin.between(1,2)
x['half']=np.where(x.date<'2026-10-01','9月','10月〜')
print('一致: 自社1位=WT◎', (x[x.ours==1].wt==1).mean().round(3))
def ev(z,w):
    z=z.assign(s=z.ours+w*z.wt+0.001*z.ours); z['rk']=z.groupby('race_key').s.rank(method='first')
    g=z.groupby('race_key')
    a1=g.apply(lambda t:t[t.rk==1].top3.iloc[0],include_groups=False).mean()
    both=g.apply(lambda t:t[t.rk<=2].top3.sum()==2,include_groups=False).mean()
    q2=g.apply(lambda t:t[t.rk<=2].top2.sum()==2,include_groups=False).mean()
    b4=g.apply(lambda t:t[t.rk<=4].top2.sum()==2,include_groups=False).mean()
    return a1,both,q2,b4
print('重み w(WT印) | 半期 | 軸1が3着内 | 軸2車とも3着内 | 2車複1点 | 2車複4車BOX')
for w in [0,0.25,0.5,1,2,100]:
    for h in ['9月','10月〜']:
        a=ev(x[x.half==h],w); print(f'{w:>5} | {h} | '+' | '.join(f'{v:.1%}' for v in a))
