import common, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
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
g=d.groupby('pgrp').agg(n=('paid','size'),d0=('race_date','min'),d1=('race_date','max'))
print(g)
print("\n日ごとの商品群構成(件数):")
p=d.pivot_table(index='race_date',columns='pgrp',values='paid',aggfunc='size').fillna(0).astype(int)
print(p.to_string())
