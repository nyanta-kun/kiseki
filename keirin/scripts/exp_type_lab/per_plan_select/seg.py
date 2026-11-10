import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_pickle(D+'live.pkl')
d=d[~((d.plan_key=='C_hit')&(d.rule_version!='ab5772d33901')&(d.race_date>='2026-10-25'))]
PL=['A_hit','B_hit','C_hit','D_hit','E_hit','F_hit','A_trio','F_line','A_ana','F_sign','B_sign','C_sign','D_sign','L_lead']
FE=['sp','smkt','ratio','axis_sum','gap','pw_ent','pred_mean_payout','n_legs','hr']
rows=[]
for p in PL:
  x=d[d.plan_key==p]
  for fe in FE:
    v=x[fe]
    if v.nunique()<3: continue
    q=pd.qcut(v.rank(method='first'),3,labels=['lo','mid','hi'])
    for h in ['H1','H2']:
      for t in ['lo','hi']:
        y=x[(q==t)&(x.half==h)]
        rows.append(dict(plan=p,fe=fe,half=h,t=t,n=len(y),dhit=y.dhit.mean(),sp=y.sp.mean(),roi=y.ret.sum()/y.budget.sum()))
r=pd.DataFrame(rows)
w=r.pivot_table(index=['plan','fe'],columns=['half','t'],values=['dhit','roi'])
out=pd.DataFrame({
 'dH1':w[('dhit','H1','hi')]-w[('dhit','H1','lo')],
 'dH2':w[('dhit','H2','hi')]-w[('dhit','H2','lo')],
 'rH1':w[('roi','H1','hi')]-w[('roi','H1','lo')],
 'rH2':w[('roi','H2','hi')]-w[('roi','H2','lo')],
 'hi_hit':(w[('dhit','H1','hi')]+w[('dhit','H2','hi')])/2,
 'hi_roi':(w[('roi','H1','hi')]+w[('roi','H2','hi')])/2,
 'lo_roi':(w[('roi','H1','lo')]+w[('roi','H2','lo')])/2,
}).round(3)
print(out)
n=d[d.plan_key.isin(PL)].groupby(['plan_key','half']).size().unstack(); print(n)
