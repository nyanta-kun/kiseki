import common, pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',60); pd.set_option('display.max_rows',200)
d = common.load()
T=d['paid'].sum()
def tab(col, name=None):
    g=d.groupby(col, dropna=False).agg(n=('paid','size'), paid=('paid','sum'), mean=('paid','mean'),
                                       med=('paid','median'), sold_rate=('nsold', lambda s:(s>0).mean()))
    g['share']=g['paid']/T
    g['n_share']=g['n']/len(d)
    g['lift']=g['mean']/d['paid'].mean()
    print(f"\n--- {name or col} ---")
    print(g.sort_values('paid',ascending=False).round(3).to_string())
tab('is_marquee','看板(決勝/特選/選抜/特秀, 準決勝除く)')
tab('is_bigevent','大会予選')
tab('fill_target','穴埋め対象(看板 or 大会予選 or cup_grade>=3)')
tab('cup_grade','開催グレード cup_grade (1=FII? 実値)')
tab('meeting','開催種別')
tab('grade','級 grade')
d['rt2']=d['race_type'].fillna('NA')
tab('rt2','race_type')
tab('venue_code','場')
tab('race_no','R番号')
tab('day_index','開催日目')
