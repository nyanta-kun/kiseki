import common, ols, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
d = common.load2()
def run(df, label, **kw):
    X,names = ols.design(df, **kw)
    for y,yl in [('lpaid','log1p(有償pt)'),('nsold','買い手数'),('sold_any','P(1本以上売れる)')]:
        res,r2,n = ols.ols(df[y],X,names)
        keep = res[~res.index.str.startswith('FE_')]
        print(f"\n## {label} / y={yl}  (n={n}, R2={r2:.3f})")
        print(keep.round(3).to_string())

base = d.dropna(subset=['n_entries']).copy()
base['rno']=base['race_no'].astype(float)
print("="*100)
print("モデル1: 日FE + レース種別のみ")
run(base,"M1", cats=[('rtg','ippan')], fe='race_date')
print("="*100)
print("モデル2: 日FE + 種別 + グレード + 級 + 開催日目 + R番号 + 出走数")
run(base,"M2", cols_num=['rno','n_entries'], cats=[('rtg','ippan'),('cg','1'),('grade','A級'),('day_index','1')], fe='race_date')
print("="*100)
print("モデル3: M2 + 開催種別(meeting)")
b3=base.dropna(subset=['meeting'])
run(b3,"M3", cols_num=['rno','n_entries'], cats=[('rtg','ippan'),('cg','1'),('grade','A級'),('day_index','1'),('meeting','day')], fe='race_date')
