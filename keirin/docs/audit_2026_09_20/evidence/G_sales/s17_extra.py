import common, pandas as pd, numpy as np
d=common.load2()
a=d[d['race_date']<'20260829']; b=d[d['race_date']>='20260829']
for lab,x in [('8/01-8/28(旧ランク)',a),('8/29-9/18(型ラボ)',b)]:
    print(f"{lab}: 日数{x['date'].nunique()} 件{len(x)} 有償pt計{x['paid'].sum():.0f} 日平均{x['paid'].sum()/x['date'].nunique():.0f} 1件{x['paid'].mean():.0f} 売れた率{(x['nsold']>0).mean():.3f}")
print("\n想定払戻の倍化効果: 2^1.239 =", round(2**1.239,2))
print("有償/総pt:", round(d['paid'].sum()/d['sold_points'].sum(),4))
print("\n1商品あたり有償pt 上位20:")
print(d.nlargest(20,'paid')[['race_key','race_date','race_label','rank_key','paid','nsold','is_conf']].to_string())
