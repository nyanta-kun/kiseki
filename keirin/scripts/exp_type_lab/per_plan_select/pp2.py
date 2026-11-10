import pandas as pd, numpy as np
import os
D=os.environ.get('PER_PLAN_DIR','./')
r=pd.read_pickle(D+'perplan.pkl')
# プランごと: セル数・|z|>=2 の数・前後半とも見込みより悪いセル(r<0.85 両半)
g=r.groupby('plan').apply(lambda x:pd.Series(dict(cells=len(x),z2=(x.z.abs()>=2).sum(),
   bad_both=((x.rH1<0.85)&(x.rH2<0.85)).sum(),good_both=((x.rH1>1.15)&(x.rH2>1.15)).sum(),
   worst=x.sort_values('z').iloc[0][['cat','lv']].str.cat(sep=':'),worst_z=round(x.z.min(),2))),include_groups=False)
print(g.to_string())
d=pd.read_pickle(D+'live.pkl'); d['sold']=d.sold.astype(str).isin(['t','True'])
d['hrb']=pd.to_datetime(d.start_at,unit='s',utc=True).dt.tz_convert('Asia/Tokyo').dt.hour
for p,cond,lab in [('L_lead',lambda x:x.hrb<11,'朝'),('F_hit',lambda x:x.race_type.str.contains('予選',na=False),'予選')]:
    x=d[d.plan_key==p]; y=x[cond(x)]
    print(p,lab,'n',len(y),'実的中',y.rhit.sum(),'見込み(Σp)',round(y.sp.sum(),1),'売った',y.sold.sum(),'売った的中',y[y.sold].dhit.sum())
for p in ['A_trio','F_sign']:
    x=d[d.plan_key==p].copy(); q=pd.qcut(x.axis_sum.rank(method='first'),3,labels=['弱','中','強'])
    for lv in ['弱','強']:
        y=x[q==lv]; print(p,'軸'+lv,'axis_sum範囲',round(y.axis_sum.min(),3),'-',round(y.axis_sum.max(),3),'n',len(y),'実',y.rhit.sum(),'見込',round(y.sp.sum(),1),'売った',y.sold.sum(),'売った的中',y[y.sold].dhit.sum(),'売った回収',round(y[y.sold].ret.sum()/max(y[y.sold].budget.sum(),1),2))
