"""対比較: 実売した的中系商品 ↔ 同じレースの同じ二軸で 2車複1点（9/25〜・7車）。
週の安定は同じ件数（対になった件数）で比べる。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
two = pd.read_pickle('two.pkl')
two = two[(two['mode'] == 'live') & (two.n_entries == 7)].set_index('race_key')
sub = pd.read_csv('subs.csv', dtype=str)
sub = sub[sub.d.str.match(r'^\d{8}$')].copy()
sub['bet'] = sub.bet.astype(float); sub['pay'] = sub.pay.astype(float)
sub = sub[sub.d >= '20260925']
sub['ret'] = sub.pay / sub.bet
sub['grp'] = np.where(sub.rank_key.str.contains('sign|ana|pay|upset'), 'high', np.where(sub.rank_key == 'L_lead', 'L_lead', 'hitp'))
p = sub.join(two[['r_q', 'r_e2d', 'type_label', 'axis_sum', 'wk']], on='race_key', how='inner')
print('対になった件数', len(p), '/ 実売', len(sub))

def s(x):
    a, b = x.ret, x.r_q; dif = b - a
    se = dif.std() / np.sqrt(len(x))
    wk = x.groupby('wk').agg(n=('ret', 'size'), ha=('ret', lambda v: (v > 1).mean()), hb=('r_q', lambda v: (v > 1).mean()),
                             ra=('ret', 'mean'), rb=('r_q', 'mean'))
    wk = wk[wk.n >= 15]
    return pd.Series({'件数': len(x), '現_的中': round(100 * (a > 1).mean(), 1), '2複_的中': round(100 * (b > 1).mean(), 1),
                      '現_回収': round(100 * a.mean(), 1), '2複_回収': round(100 * b.mean(), 1),
                      '差CI': f'[{100*(dif.mean()-1.96*se):+.0f},{100*(dif.mean()+1.96*se):+.0f}]',
                      '週数': len(wk), '現_週回収SD': round(100 * wk.ra.std(), 1), '2複_週回収SD': round(100 * wk.rb.std(), 1),
                      '現_週回収最低': round(100 * wk.ra.min()), '2複_週回収最低': round(100 * wk.rb.min()),
                      '現_週的中最低': round(100 * wk.ha.min()), '2複_週的中最低': round(100 * wk.hb.min())})

pd.set_option('display.width', 260); pd.set_option('display.max_columns', 30)
h = p[p.grp == 'hitp']
print('== 的中系 全体（1レース1件に直さず、商品1件ごと）')
print(s(h).to_frame().T.to_string())
print('== プラン別')
print(h.groupby('rank_key').apply(s).to_string())
h2 = h.assign(half=np.where(h.d < '20261023', '前半', '後半'))
print('== 前後半')
print(h2.groupby('half').apply(s).to_string())
print('== 高額系（参考: 同じレースを2車複にしたら）')
print(p[p.grp == 'high'].groupby('rank_key').apply(s)[['件数', '現_的中', '2複_的中', '現_回収', '2複_回収', '差CI']].to_string())
# 型A: 10/24 以降は的中系が無い。型A の全レースで 2車複
two_l = two.reset_index()
a = two_l[(two_l.type_label == 'A') & (two_l.race_date >= '2026-09-25')]
a = a.assign(half=np.where(a.race_date < '2026-10-23', '前半', '後半'))
print('== 型A 全レース 2車複1点（9/25〜）')
print(a.groupby('half').apply(lambda x: pd.Series({'R': len(x), '的中': round(100*(x.r_q>1).mean(),1), '回収': round(100*x.r_q.mean(),1),
      'CI': f'[{100*(x.r_q.mean()-1.96*x.r_q.std()/np.sqrt(len(x))):.0f},{100*(x.r_q.mean()+1.96*x.r_q.std()/np.sqrt(len(x))):.0f}]'})).to_string())
