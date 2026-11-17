import pandas as pd, numpy as np
pd.set_option('display.width',250)
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_pickle(D+'live.pkl')
d['sold']=d.sold.astype(str).isin(['t','True'])
H=['A_hit','B_hit','C_hit','D_hit','E_hit','F_hit','A_trio','F_line']
x=d[d.plan_key.isin(H)].copy()
# plan-level calibration so categories are compared vs plan's own ratio
k=x.groupby('plan_key').apply(lambda g:g.rhit.sum()/g.sp.sum()); x['exp']=x.sp*x.plan_key.map(k)
print('plan actual/Σp:',k.round(2).to_dict())
x['rt']=x.race_type.fillna('?').str.replace(r'.*(予選|特選|選抜|一般|準決勝|決勝|ガールズ).*',lambda m:m.group(1),regex=True)
x['hrb']=pd.cut(pd.to_datetime(x.start_at,utc=True).dt.tz_convert('Asia/Tokyo').dt.hour,[0,11,14,17,20,24],labels=['~10','11-13','14-16','17-19','20-'],right=False).astype(str)
x['ax']=pd.qcut(x.axis_sum,4,labels=['ax1lo','ax2','ax3','ax4hi'])
for c in ['rt','n_entries','day_index','hrb','type_label','ax','arare']:
    g=x.groupby([c,'half'],observed=True).agg(n=('rhit','size'),a=('rhit','sum'),e=('exp','sum')).unstack()
    g=g[(g[('n','H1')]>=80)&(g[('n','H2')]>=80)]
    out=pd.DataFrame({'nH1':g[('n','H1')],'rH1':(g[('a','H1')]/g[('e','H1')]).round(2),'nH2':g[('n','H2')],'rH2':(g[('a','H2')]/g[('e','H2')]).round(2)})
    # approx SE of ratio ~ sqrt(e)/e
    out['seH2']=(1/np.sqrt(g[('e','H2')])).round(2)
    print('\n==',c); print(out)
