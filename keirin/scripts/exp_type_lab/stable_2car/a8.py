"""対比較（実売した的中系 ↔ 同じレースを三連複で）と、構成ごとの「先週の数字」の分布（実売 9/25〜 の日を7日ずつ再標本化）。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
rng = np.random.default_rng(0)
A = pd.read_pickle('arms.pkl')
sub = pd.read_csv('subs.csv', dtype=str)
sub = sub[sub.d.str.match(r'^\d{8}$')].copy()
sub['ret'] = sub.pay.astype(float) / sub.bet.astype(float)
sub = sub[sub.d >= '20260925']
sub['high'] = sub.rank_key.str.contains('sign|ana|pay|upset')
CAND = {'B_hit': 'trio_3_du', 'C_hit': 'trio_3_du', 'D_hit': 'trio_4_du', 'E_hit': 'trio_5_du', 'F_hit': 'trio_3_eq'}
p = sub.join(A, on='race_key', how='inner')
print('== 対比較（実売の的中系・同じレース）')
rows = []
for rk, arm in CAND.items():
    x = p[p.rank_key == rk].dropna(subset=[arm]); d = x[arm] - x.ret; se = d.std() / np.sqrt(len(x))
    wk = x.groupby('wk').agg(n=('ret', 'size'), a=('ret', 'mean'), b=(arm, 'mean')); wk = wk[wk.n >= 15]
    rows.append([rk, arm, len(x), round(100*(x.ret>1).mean(),1), round(100*(x[arm]>1).mean(),1), round(100*x.ret.mean(),1), round(100*x[arm].mean(),1),
                 f'[{100*(d.mean()-1.96*se):+.0f},{100*(d.mean()+1.96*se):+.0f}]', f'{100*wk.a.std():.1f} / {100*wk.b.std():.1f}', f'{100*wk.a.min():.0f} / {100*wk.b.min():.0f}'])
print(pd.DataFrame(rows, columns=['プラン', '置き換え', '件数', '今の的中', '三連複の的中', '今の回収', '三連複の回収', '差CI', '週回収SD 今/案', '週回収最低 今/案']).to_string(index=False))

base = sub[~sub.rank_key.isin(['L_lead', 'A_hit'])].copy()
def replaced(df, plans, col='arm'):
    s = df.copy()
    for rk in plans:
        m = (s.rank_key == rk) & s.race_key.isin(A.index)
        s.loc[m, 'ret'] = s.loc[m, 'race_key'].map(A[CAND[rk]] if col == 'arm' else A['r_q']).values
    return s.dropna(subset=['ret'])
sold = set(sub.race_key)
ta = A[(A.type_label == 'A') & (A.race_date >= '2026-09-25') & ~A.index.isin(sold)]
def addA(df, col):
    ad = pd.DataFrame({'d': ta.race_date.str.replace('-', ''), 'race_key': ta.index, 'rank_key': 'A_new', 'ret': ta[col].values, 'high': False})
    return pd.concat([df, ad[ad.d.isin(set(df.d))]]).dropna(subset=['ret'])
P = {'P0 現行': base,
     'T1 B/C/D→三連複': replaced(base, ['B_hit', 'C_hit', 'D_hit']),
     'T2 T1＋型A 三連複3点': addA(replaced(base, ['B_hit', 'C_hit', 'D_hit']), 'trio_3_du'),
     'T3 T2＋E→三連複5点': addA(replaced(base, ['B_hit', 'C_hit', 'D_hit', 'E_hit']), 'trio_3_du'),
     '参考 2車複案(P2)': addA(replaced(base, ['B_hit', 'C_hit', 'D_hit'], col='q'), 'r_q')}
rows = []
for nm, x in P.items():
    days = sorted(set(x.d)); g = {d: v.ret.values for d, v in x.groupby('d')}
    H, R = [], []
    for _ in range(20000):
        v = np.concatenate([g[d] for d in rng.choice(days, 7, replace=True)]); H.append((v > 1).mean()); R.append(v.mean())
    H, R = np.array(H), np.array(R)
    rows.append([nm, round(len(x)/len(days),1), round(100*(x.ret>1).mean(),1), round(100*x.ret.mean(),1),
                 f'{100*np.quantile(R,.1):.0f}〜{100*np.quantile(R,.9):.0f}', f'{100*(R<.65).mean():.0f}%', f'{100*(R>=.75).mean():.0f}%',
                 round(100*np.quantile(H,.1),1), f'{100*(H<.20).mean():.0f}%'])
print(pd.DataFrame(rows, columns=['構成', '件/日', '表示的中', '回収', '週回収10〜90%点', '週回収<65%', '週回収≥75%', '週的中10%点', '週的中<20%']).to_string(index=False))
