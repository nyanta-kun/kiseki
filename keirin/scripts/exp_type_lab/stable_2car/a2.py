"""二軸を2車券にした場合の的中率・回収率と週ごとの安定性（売った軸＝発走前に決めた軸）。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
df = pd.read_csv('two.csv', dtype=str)
df = df[df.race_key.str.match(r'^\d{8}_')].copy()
for c in ['n_entries','axis_sum','arare','gap','axis1','axis2','void_refund','q','w','e12','e21','qfav']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df[(df.void_refund.fillna(0) == 0)]
fin = df.win_combo.str.split('-', expand=True)
df['f1'] = pd.to_numeric(fin[0]); df['f2'] = pd.to_numeric(fin[1]); df['f3'] = pd.to_numeric(fin[2])
a, b = df.axis1, df.axis2
top2 = ((a == df.f1) & (b == df.f2)) | ((a == df.f2) & (b == df.f1))
top3 = a.isin([0]) # placeholder
df['hit_q'] = top2
df['hit_w'] = df.apply(lambda r: {r.axis1, r.axis2} <= {r.f1, r.f2, r.f3}, axis=1)
df['hit_e12'] = (a == df.f1) & (b == df.f2)
df['hit_e21'] = (b == df.f1) & (a == df.f2)
bad = (df[['q','w','e12','e21']] >= 9999).any(axis=1) | df[['q','w','e12','e21']].isna().any(axis=1)
df = df[~bad].copy()
# 返り（1万円あたりの倍率）
df['r_q'] = np.where(df.hit_q, df.q, 0.0)
df['r_w'] = np.where(df.hit_w, df.w, 0.0)
df['r_e12'] = np.where(df.hit_e12, df.e12, 0.0)
df['r_e2'] = (np.where(df.hit_e12, df.e12, 0.0) + np.where(df.hit_e21, df.e21, 0.0)) / 2  # 2車単 裏表 均等
# 2車単 裏表 ダッチ（どちらが来ても同額）
inv = 1/df.e12 + 1/df.e21
df['r_e2d'] = np.where(df.hit_e12 | df.hit_e21, 1/inv, 0.0)
df['d'] = pd.to_datetime(df.race_date)
df['wk'] = df.d.dt.to_period('W')
df.to_pickle('two.pkl')

def summ(x, label):
    out = []
    for k, nm in [('r_q','2車複1点'),('r_w','ワイド1点'),('r_e12','2車単1点(軸1→軸2)'),('r_e2','2車単裏表均等'),('r_e2d','2車単裏表ダッチ')]:
        ret = x[k]; hit = ret > 1.0
        wk = x.groupby('wk').agg(n=(k,'size'), h=(k, lambda s: (s>1).mean()), r=(k,'mean'))
        wk = wk[wk.n >= 50]
        se = ret.std()/np.sqrt(len(ret))
        out.append((label, nm, len(x), round(100*hit.mean(),1), round(100*ret.mean(),1), f'[{100*(ret.mean()-1.96*se):.0f},{100*(ret.mean()+1.96*se):.0f}]',
                    round(100*wk.h.std(),1), round(100*wk.r.std(),1), round(100*wk.r.min(),0), len(wk)))
    return out

rows = []
for mode in ['live','paper']:
    x = df[(df['mode'] == mode) & (df.n_entries == 7)]
    rows += summ(x, f'{mode} 7車 全')
    q = x.axis_sum.quantile([0.5, 0.8])
    rows += summ(x[x.axis_sum >= q[0.5]], f'{mode} 7車 軸強 上位50%')
    rows += summ(x[x.axis_sum >= q[0.8]], f'{mode} 7車 軸強 上位20%')
    x9 = df[(df['mode'] == mode) & (df.n_entries == 9)]
    if len(x9) > 300: rows += summ(x9, f'{mode} 9車 全')
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 20)
print(pd.DataFrame(rows, columns=['窓','買い方','R','表示的中%','回収%','95%CI','週的中SD','週回収SD','週回収最低','週数']).to_string(index=False))
print(df.groupby(['mode','n_entries']).size())
