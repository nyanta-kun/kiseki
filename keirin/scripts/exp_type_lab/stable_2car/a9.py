"""三連複 二軸＋相手の可変点数: 相手を指数順に最大 kmax 点とり、朝オッズのダッチ倍率 1/Σ(1/朝オッズ) が m 未満なら
指数の低い相手から1点ずつ外す（最低 kmin 点）。それでも m 未満なら「見送り」版と「最低点数で買う」版を比べる。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
two = pd.read_pickle('two.pkl'); two = two[(two['mode'] == 'live') & (two.n_entries == 7)].set_index('race_key')
legs = pd.read_csv('legs3.csv', dtype={'combination': str}); legs = legs[legs.race_key.str.match(r'^\d{8}_') & (legs.bet_type == 'trio')]
legs['fin'] = pd.to_numeric(legs.fin, errors='coerce'); legs['mor'] = pd.to_numeric(legs.mor, errors='coerce')
L = {k: g for k, g in legs.groupby('race_key')}
def stakes(o):
    w = (1 / o) / (1 / o).sum(); return np.maximum(100, np.floor(w * 10000 / 100) * 100)
res = []
for rk, r in two.iterrows():
    g = L.get(rk)
    if g is None or (g.fin >= 9999).any() or g.mor.isna().any(): continue
    a, b = int(r.axis1), int(r.axis2)
    others = [int(x) for x in str(r.p3_order).split('-') if int(x) not in (a, b)]
    g = g.assign(x=g.combination.map(lambda c: [int(v) for v in c.split('-') if int(v) not in (a, b)][0])).set_index('x')
    win = '-'.join(map(str, sorted([int(r.f1), int(r.f2), int(r.f3)])))
    out = {'race_key': rk}
    for m in [0, 1.1, 1.3, 1.5, 2.0, 2.5]:
        for kmax, kmin in [(3, 1), (3, 2), (4, 1)]:
            k = kmax
            while k > kmin and 1 / (1 / g.loc[others[:k], 'mor']).sum() < m: k -= 1
            sel = g.loc[others[:k]]; mult = 1 / (1 / sel.mor).sum()
            s = stakes(sel.mor.values); hit = sel.combination.values == win
            v = (s[hit] * sel.fin.values[hit]).sum() / s.sum()
            out[f'm{m}_k{kmax}-{kmin}_buy'] = v
            out[f'm{m}_k{kmax}-{kmin}_skip'] = v if mult >= m else np.nan
    res.append(out)
R = pd.DataFrame(res).set_index('race_key').join(two[['race_date', 'wk', 'type_label']])
R.to_pickle('adapt.pkl')
R['half'] = np.where(R.race_date < '2026-10-10', 'H1', 'H2')
x = R[R.type_label.isin(['A', 'B', 'C'])]
rows = []
for c in [c for c in R.columns if c.startswith('m')]:
    v = x[c]; n = v.notna().sum(); vv = v.dropna(); se = vv.std() / np.sqrt(n)
    wk = x.groupby('wk')[c].mean()
    h = x.groupby('half')[c].agg(['mean', lambda t: (t.dropna() > 1).mean()])
    rows.append([c, n, round(100 * (vv > 1).mean(), 1), round(100 * ((vv > 0) & (vv <= 1)).mean(), 1), round(100 * vv.mean(), 1),
                 f'[{100*(vv.mean()-1.96*se):.0f},{100*(vv.mean()+1.96*se):.0f}]', f'{100*h.iloc[0,1]:.0f}/{100*h.iloc[1,1]:.0f}',
                 f'{100*h.iloc[0,0]:.0f}/{100*h.iloc[1,0]:.0f}', round(100 * wk.std(), 1), round(100 * wk.min())])
pd.set_option('display.width', 250)
print(pd.DataFrame(rows, columns=['腕', 'R', '表示的中', 'ガミ', '回収', 'CI', '的中 前/後', '回収 前/後', '週回収SD', '週回収最低']).to_string(index=False))
