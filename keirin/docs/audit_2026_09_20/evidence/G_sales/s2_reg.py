import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',60); pd.set_option('display.max_rows',300)
d = common.load()
d['lpaid']=np.log1p(d['paid'])
# race_type グループ化
def rtg(rt):
    if not isinstance(rt,str): return 'other'
    if '準決勝' in rt: return 'semifinal'
    if '決勝' in rt: return 'final'
    if '特選' in rt or '選抜' in rt or '特秀' in rt or '優秀' in rt: return 'tokusen'
    if '予選' in rt: return 'yosen'
    if '一般' in rt: return 'ippan'
    return 'other'
d['rtg']=d['race_type'].apply(rtg)
print(d.groupby('rtg').agg(n=('paid','size'),mean=('paid','mean'),share=('paid',lambda s:s.sum()/d['paid'].sum())).round(3))

print("\n### 日FE入り: 日内でどのレースが売れるか (y=log1p(有償pt)) ###")
d['cg']=d['cup_grade'].astype(str)
sub = d.dropna(subset=['start_hour','n_entries']).copy()
sub['rno']=sub['race_no'].astype(float)
X,names = ols.design(sub, cols_num=['rno','n_entries','start_hour'],
    cats=[('rtg','ippan'),('cg','1'),('grade','A級'),('meeting','day'),('day_index','1')], fe='race_date')
res,r2,n = ols.ols(sub['lpaid'], X, names)
print(res[~res.index.str.startswith('FE_')].round(3).to_string())
print("R2",round(r2,3),"n",n)

print("\n### 同じ設計で y=n_sold(買い手数) ###")
res2,r22,_ = ols.ols(sub['nsold'], X, names)
print(res2[~res2.index.str.startswith('FE_')].round(3).to_string())
print("R2",round(r22,3))
