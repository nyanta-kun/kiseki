import os
import pandas as pd, numpy as np
x=pd.read_pickle(os.path.join(os.environ.get('PER_PLAN_DIR','./'),'wt_box.pkl'))
for nm in ['WT','自社','市場']:
  for k,pts in [(2,1),(3,3),(4,6)]:
    h=x[f'{nm}{k}_hit'].astype(float); r=x[f'{nm}{k}_ret'].astype(float)
    print(f'{nm} {k}車BOX {pts}点: 的中 {h.mean():.1%} / 払戻>賭け金 {(r>1).mean():.1%} / ガミ {((h==1)&(r<=1)).mean():.1%} / 回収 {r.mean():.1%}')
h=x[x.date>='2026-10-01']; print('10月以降 WT4:',f"{h['WT4_hit'].astype(float).mean():.1%}", len(h))
