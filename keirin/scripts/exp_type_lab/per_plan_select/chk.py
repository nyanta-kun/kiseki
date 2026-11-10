import pandas as pd, numpy as np
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_pickle(D+'live.pkl')
d['sold']=d.sold.astype(str).isin(['t','True'])
d['tgt']=0.75*d.budget/d.smkt
for p in ['A_hit','B_hit','C_hit','D_hit','E_hit','F_hit','A_trio']:
    x=d[d.plan_key==p]
    print(p,'corr(smkt, log tgt)=%.2f'%np.corrcoef(x.smkt,np.log(x.tgt))[0,1],
          'corr(smkt, payout|hit)=%.2f'%x[x.rhit==1][['smkt','payout']].corr().iloc[0,1])
rng=np.random.default_rng(0)
def boot(a,b,stat,B=4000):
    out=[]
    for _ in range(B):
        ia=rng.integers(0,len(a),len(a)); ib=rng.integers(0,len(b),len(b))
        out.append(stat(a.iloc[ia])-stat(b.iloc[ib]))
    return np.percentile(out,[2.5,97.5])
roi=lambda y:y.ret.sum()/y.budget.sum()
x=d[d.plan_key=='A_trio'].copy()
cut=x[x.half=='H1'].axis_sum.quantile(1/3)
print('\nA_trio axis_sum H1 lower-tercile cut =',round(cut,4))
for h in ['H1','H2']:
    y=x[x.half==h]; k=y[y.axis_sum>cut]; s=y[y.axis_sum<=cut]
    for nm,z in [('all',y),('keep',k),('skip',s)]:
        print(h,nm,len(z),'hit=%.3f sp=%.3f roi=%.3f pay|hit=%.0f sold=%d'%(z.dhit.mean(),z.sp.mean(),roi(z),z[z.rhit==1].payout.mean(),z.sold.sum()))
    print(' keep-skip roi CI',boot(k,s,roi).round(3),' hit CI',boot(k,s,lambda q:q.dhit.mean()).round(3))
    # outlier: drop top1% payouts
    t=y.payout.quantile(.99); y2=y[y.payout<=t]
    print(' drop top1pct: keep roi %.3f skip roi %.3f'%(roi(y2[y2.axis_sum>cut]),roi(y2[y2.axis_sum<=cut])))
# sold A_trio only
z=x[x.sold]; print('\nsold A_trio n',len(z),'axis_sum<=cut among sold',(z.axis_sum<=cut).sum(), 'hit keep/skip',z[z.axis_sum>cut].dhit.mean().round(3), z[z.axis_sum<=cut].dhit.mean().round(3))
