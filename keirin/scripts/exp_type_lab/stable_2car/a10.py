"""紙上 6/27〜8/26（実売前）で 三連複 二軸＋相手k点 ダッチ（朝オッズ）を型別に。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
two = pd.read_pickle('two.pkl'); two = two[(two['mode'] == 'paper') & (two.n_entries == 7)].set_index('race_key')
legs = pd.read_csv('legs3p.csv', dtype={'combination': str}); legs = legs[legs.race_key.str.match(r'^\d{8}_')]
legs['fin'] = pd.to_numeric(legs.fin, errors='coerce'); legs['mor'] = pd.to_numeric(legs.mor, errors='coerce')
L = {k: g for k, g in legs.groupby('race_key')}
rows = []
for rk, r in two.iterrows():
    g = L.get(rk)
    if g is None or len(g) != 5 or (g.fin >= 9999).any() or g.mor.isna().any(): continue
    a, b = int(r.axis1), int(r.axis2)
    others = [int(x) for x in str(r.p3_order).split('-') if int(x) not in (a, b)]
    g = g.assign(x=g.combination.map(lambda c: [int(v) for v in c.split('-') if int(v) not in (a, b)][0])).set_index('x')
    win = '-'.join(map(str, sorted([int(r.f1), int(r.f2), int(r.f3)])))
    out = {'race_key': rk, 'type_label': r.type_label, 'r_q': r.r_q}
    for k in [2, 3, 4, 5]:
        sel = g.loc[others[:k]]; w = (1 / sel.mor) / (1 / sel.mor).sum(); s = np.maximum(100, np.floor(w.values * 100) * 100)
        hit = sel.combination.values == win
        out[f'trio_{k}_du'] = (s[hit] * sel.fin.values[hit]).sum() / s.sum()
    rows.append(out)
P = pd.DataFrame(rows)
P['grp'] = np.where(P.type_label.isin(['A', 'B', 'C']), 'A/B/C', P.type_label)
f = lambda v: f'{100*(v>1).mean():.1f}% / {100*v.mean():.1f} [{100*(v.mean()-1.96*v.std()/np.sqrt(len(v))):.0f},{100*(v.mean()+1.96*v.std()/np.sqrt(len(v))):.0f}]'
print(P.groupby('grp').apply(lambda x: pd.Series({'R': len(x), **{c: f(x[c]) for c in ['r_q', 'trio_2_du', 'trio_3_du', 'trio_4_du', 'trio_5_du']}})).to_string())
