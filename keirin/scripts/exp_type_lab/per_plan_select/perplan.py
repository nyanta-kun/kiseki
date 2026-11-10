import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_pickle(D+'live.pkl')
d=d[~((d.plan_key=='C_hit')&(d.race_date>='2026-10-25'))]   # C_hit は旧構造だけ（新構造は n 不足）
d['hrb']=pd.cut(pd.to_datetime(d.start_at,unit='s',utc=True).dt.tz_convert('Asia/Tokyo').dt.hour,[0,11,14,17,20,25],right=False,labels=['朝~10','昼11-13','午後14-16','夕17-19','夜20-']).astype(str)
d['rt']=d.race_type.fillna('?').str.extract(r'(予選|特選|選抜|一般|準決勝|決勝|ガールズ)')[0].fillna('他')
d['grade_']=d.grade.fillna('?')
d['day']=d.day_index.astype(str)
d['arare_']=pd.cut(d.arare,[-9,-1,0,1,9],labels=['≤-1','0','1','≥2']).astype(str)
d['venue']=d.venue_name
PL=['B_hit','C_hit','D_hit','E_hit','F_hit','A_trio','F_line','A_ana','F_sign','B_sign','C_sign','D_sign','L_lead']
CATS=['rt','day','hrb','grade_','arare_','venue']
rows=[]
for p in PL:
  x=d[d.plan_key==p].copy()
  x['ax']=pd.qcut(x.axis_sum.rank(method='first'),3,labels=['軸弱','軸中','軸強']).astype(str)
  k=x.rhit.sum()/x.sp.sum(); x['e']=x.sp*k          # プラン自身の平均の外し方を除く
  base_roi=x.ret.sum()/x.budget.sum()
  for c in CATS+['ax']:
    for lv,g in x.groupby(c):
      if len(g)<40: continue
      r={'plan':p,'cat':c,'lv':lv,'n':len(g)}
      for h in ['H1','H2']:
        y=g[g.half==h]; r['r'+h]=y.rhit.sum()/max(y.e.sum(),1e-9); r['roi'+h]=y.ret.sum()/y.budget.sum(); r['n'+h]=len(y)
      v=(g.e*(1-g.e)).sum(); r['z']=(g.rhit.sum()-g.e.sum())/np.sqrt(v)
      r['hit']=g.dhit.mean(); r['roi']=g.ret.sum()/g.budget.sum(); r['roi_plan']=base_roi
      rows.append(r)
r=pd.DataFrame(rows)
r['same']=np.sign(r.rH1-1)==np.sign(r.rH2-1)
print('セル数',len(r),' |z|>=2:',(r.z.abs()>=2).sum(),' (偶然なら約',round(len(r)*0.046,1),')')
f=r[(r.z.abs()>=2)&r.same&(r.nH1>=15)&(r.nH2>=15)].sort_values('z')
print(f[['plan','cat','lv','n','nH1','nH2','rH1','rH2','z','hit','roi','roi_plan','roiH1','roiH2']].round(2).to_string())
r.to_pickle(D+'perplan.pkl')
