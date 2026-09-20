import common, pandas as pd, numpy as np
pd.set_option('display.width',250)
d=common.load2()
e=d[d['race_date']>='20260829']
print("8/29以降: 件/日", round(len(e)/e['date'].nunique(),1))
print("予選の割合:", round((e['rtg']=='yosen').mean(),3), " うちデイ/ナイター:",
      round(((e['rtg']=='yosen')&(e['meeting'].isin(['day','nighter']))).mean(),3))
z=e[(e['rtg']=='yosen')&(e['meeting'].isin(['day','nighter']))]
print("予選×デイ/ナイター:", len(z), "件 売上", z['paid'].sum(), f"({z['paid'].sum()/e['paid'].sum():.1%})",
      " 1件", round(z['paid'].mean(),0), " 無売上率", round((z['nsold']==0).mean(),3))
print("\n直近7日(09/12-09/18):")
r=d[d['race_date']>='20260912']
print(" 件/日", round(len(r)/r['date'].nunique(),1), " 日売上", round(r['paid'].sum()/r['date'].nunique(),0),
      " 個/R", round(r['nsold'].mean(),2), " 無売上率", round((r['nsold']==0).mean(),3))
