import json, pandas as pd, numpy as np, sys
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_csv(D+'live.csv')
def f(s):
    L=json.loads(s); sp=sum(l['prob'] for l in L)
    sm=sum(0.75/l['pred_odds'] for l in L if l.get('pred_odds'))
    st=sum(l.get('stake',0) for l in L)
    # stake-weighted payout if hit, min payout
    pays=[l['stake']*l['pred_odds'] for l in L if l.get('pred_odds')]
    return pd.Series(dict(sp=sp,smkt=sm,minpay=min(pays) if pays else np.nan))
d=pd.concat([d,d.legs.apply(f)],axis=1)
d['ratio']=d.sp/d.smkt
d['ret']=d.payout.fillna(0)+d.void_refund.fillna(0)
d['dhit']=(d.ret>d.budget).astype(int)
d['rhit']=(d.payout.fillna(0)>0).astype(int)
d['half']=np.where(d.race_date<'2026-10-17','H1','H2')
d['hr']=pd.to_datetime(d.start_at).dt.hour
d.drop(columns=['legs']).to_pickle(D+'live.pkl')
print(d.groupby('plan_key').agg(n=('dhit','size'),sold=('sold','sum'),dhit=('dhit','mean'),sp=('sp','mean'),smkt=('smkt','mean'),roi=('ret','sum'),b=('budget','sum')).assign(roi=lambda x:x.roi/x.b).round(3))
