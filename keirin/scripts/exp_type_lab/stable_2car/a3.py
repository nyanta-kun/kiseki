"""2車複1点の分解: 市場1番人気との一致・オッズ帯・固定閾値（paper で決めた軸強の閾値を live に当てる）・前後半。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
df = pd.read_pickle('two.pkl')
live = df[(df['mode'] == 'live') & (df.n_entries == 7)].copy()
paper = df[(df['mode'] == 'paper') & (df.n_entries == 7)].copy()

def s(x, k='r_q'):
    r = x[k]; se = r.std()/np.sqrt(max(len(r),1))
    return pd.Series({'R': len(x), '的中%': round(100*(r>1).mean(),1), '回収%': round(100*r.mean(),1),
                      'CI下': round(100*(r.mean()-1.96*se),0), 'CI上': round(100*(r.mean()+1.96*se),0)})

live['is_fav'] = (live.q <= live.qfav + 1e-9)
print('== 軸ペアが2車複の最終1番人気か')
print(live.groupby('is_fav').apply(s))
live['band'] = pd.cut(live.q, [0, 2, 3, 5, 8, 15, 1e9])
print('== 2車複オッズ帯（最終オッズ・選別には使えない参考）')
print(live.groupby('band', observed=True).apply(s))
thr50 = paper.axis_sum.quantile(0.5); thr80 = paper.axis_sum.quantile(0.8)
print('paper で決めた閾値', round(thr50,4), round(thr80,4))
live['half'] = np.where(live.race_date < '2026-10-10', '前半', '後半')
for nm, m in [('全', live.axis_sum > -1), ('軸強(paper上位50%閾値)', live.axis_sum >= thr50), ('軸強(paper上位20%閾値)', live.axis_sum >= thr80)]:
    print('==', nm)
    print(live[m].groupby('half').apply(s))
print('== 型別（live・全）')
print(live.groupby('type_label').apply(s))
print('== 種別（live・全）')
print(live.groupby('race_type').apply(s).sort_values('R', ascending=False).head(12))
