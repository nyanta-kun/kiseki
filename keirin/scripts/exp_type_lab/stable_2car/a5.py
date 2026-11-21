"""商品構成ごとの「先週の的中率・回収率」の分布（実売 9/25〜 の日を7日ずつ再標本化）。
P0: 現行（L_lead 停止後）
P1: B/C/D_hit を同じレースの二軸 2車複1点へ置き換え（E/F_hit・F_line・A_trio・高額系はそのまま）
P2: P1 ＋ 型A の売っていないレースに 2車複1点を足す
P3: P1 から高額系を外す（参考：安定の上限）
P4: 現行から高額系を外す（参考）
"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
rng = np.random.default_rng(0)
two = pd.read_pickle('two.pkl')
two = two[(two['mode'] == 'live') & (two.n_entries == 7)]
sub = pd.read_csv('subs.csv', dtype=str)
sub = sub[sub.d.str.match(r'^\d{8}$')].copy()
sub['ret'] = sub.pay.astype(float) / sub.bet.astype(float)
sub = sub[(sub.d >= '20260925') & (sub.rank_key != 'L_lead') & (sub.rank_key != 'A_hit')]  # A_hit は 10/24 停止・L_lead は 11/19 停止
sub['high'] = sub.rank_key.str.contains('sign|ana|pay|upset')
q = two.set_index('race_key').r_q
REPL = {'B_hit', 'C_hit', 'D_hit'}
s1 = sub.copy()
m = s1.rank_key.isin(REPL) & s1.race_key.isin(q.index)
s1.loc[m, 'ret'] = s1.loc[m, 'race_key'].map(q).values
s1.loc[m, 'rank_key'] = s1.loc[m, 'rank_key'].str.replace('_hit', '_q2')
sold = set(sub.race_key)
ta = two[(two.type_label == 'A') & (two.race_date >= '2026-09-25') & ~two.race_key.isin(sold)]
add = pd.DataFrame({'d': ta.race_date.str.replace('-', ''), 'race_key': ta.race_key, 'rank_key': 'A_q2', 'ret': ta.r_q.values, 'high': False})
add = add[add.d.isin(set(sub.d))]
P = {'P0 現行': sub, 'P1 B/C/D→2車複': s1, 'P2 P1＋型A 2車複': pd.concat([s1, add]),
     'P3 P1−高額系': s1[~s1.high], 'P4 現行−高額系': sub[~sub.high]}
rows = []
for nm, x in P.items():
    days = sorted(set(x.d)); g = {d: v.ret.values for d, v in x.groupby('d')}
    H, R = [], []
    for _ in range(20000):
        pick = rng.choice(days, 7, replace=True)
        v = np.concatenate([g[d] for d in pick]); H.append((v > 1).mean()); R.append(v.mean())
    H, R = np.array(H), np.array(R)
    rows.append({'構成': nm, '件/日': round(len(x) / len(days), 1), '的中%': round(100 * (x.ret > 1).mean(), 1), '回収%': round(100 * x.ret.mean(), 1),
                 '週回収 10%点': round(100 * np.quantile(R, .1)), '週回収 中央': round(100 * np.median(R)), '週回収 90%点': round(100 * np.quantile(R, .9)),
                 '週回収<65%の確率': f'{100*(R<.65).mean():.0f}%', '週回収≥75%の確率': f'{100*(R>=.75).mean():.0f}%',
                 '週的中 10%点': round(100 * np.quantile(H, .1), 1), '週的中<20%の確率': f'{100*(H<.20).mean():.0f}%'})
pd.set_option('display.width', 260); pd.set_option('display.max_columns', 30)
print(pd.DataFrame(rows).to_string(index=False))
# 分散の内訳（1日の回収率の分散 ≈ Σ 件数×1件の分散 / 件数合計²）
x = sub; n = x.groupby('high').ret.agg(['size', 'var'])
tot = (n['size'] * n['var']).sum()
print('1日の回収のぶれの内訳（現行）: 高額系', f"{100*n.loc[True,'size']*n.loc[True,'var']/tot:.0f}%", '件数比', f"{100*n.loc[True,'size']/n['size'].sum():.0f}%",
      '高額系の回収', round(100 * x[x.high].ret.mean(), 1), '的中系の回収', round(100 * x[~x.high].ret.mean(), 1))
