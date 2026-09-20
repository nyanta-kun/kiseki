import ols, pandas as pd, numpy as np
from scipy import stats
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
g=pd.read_csv('daily_series.csv', parse_dates=['date'])
g['ln']=np.log(g['n_races'])
# 過去 k 日の実績（当日は含めない）
for k in [1,3,7]:
    for c,name in [('hit','hit'),('hit_excl','hitx'),('roi','roi'),('n_big','big'),('maxpay','maxpay')]:
        if c in ('n_big',):
            g[f'{name}_L{k}']=g[c].shift(1).rolling(k).sum()
        elif c=='maxpay':
            g[f'{name}_L{k}']=np.log1p(g[c].shift(1).rolling(k).max())
        elif c=='roi':
            num=g['payout'].shift(1).rolling(k).sum(); den=g['stake'].shift(1).rolling(k).sum()
            g[f'{name}_L{k}']=num/den
        else:
            num=(g[c]*g['n_races']).shift(1).rolling(k).sum(); den=g['n_races'].shift(1).rolling(k).sum()
            g[f'{name}_L{k}']=num/den
base_cols=['ln','share_final','share_hp','t']
def fit(extra, dfsub=None, label=''):
    df=(dfsub if dfsub is not None else g).dropna(subset=base_cols+extra+['lpaid']).copy()
    X,names=ols.design(df, cols_num=base_cols+extra)
    res,r2,n=ols.ols(df['lpaid'],X,names)
    print(f"\n[{label}] n={n} R2={r2:.3f}")
    print(res.round(3).to_string())
    return res
print("="*90); print("ベースライン（構成と線形トレンドのみ）")
fit([], label='base')
for k in [1,3,7]:
    fit([f'hit_L{k}'], label=f'+的中率(ガミ含む) 直近{k}日')
    fit([f'hitx_L{k}'], label=f'+表示的中率(ガミ除く) 直近{k}日')
    fit([f'roi_L{k}'], label=f'+回収率 直近{k}日')
    fit([f'big_L{k}'], label=f'+10万+的中の件数 直近{k}日')
    fit([f'maxpay_L{k}'], label=f'+log最高払戻 直近{k}日')
print("="*90)
print("全部同時（直近3日）")
fit(['hitx_L3','roi_L3','big_L3'], label='joint L3')
print("="*90)
print("9月のみ（商品構成が安定した期間）")
g9=g[g['date']>='2026-08-29']
for k in [1,3,7]:
    fit([f'hitx_L{k}'], dfsub=g9, label=f'9月 +表示的中 L{k}')
    fit([f'big_L{k}'], dfsub=g9, label=f'9月 +10万+件数 L{k}')
