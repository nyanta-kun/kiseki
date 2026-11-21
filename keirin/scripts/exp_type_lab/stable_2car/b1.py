"""本番の配分（予測オッズのダッチ）と分析の配分（朝オッズのダッチ）の差、と入稿ゲートが何割切るか。"""
import os, json, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
legs = pd.read_csv('legs3.csv', dtype={'combination': str})
legs = legs[legs.race_key.str.match(r'^\d{8}_') & (legs.bet_type == 'trio')]
legs['fin'] = pd.to_numeric(legs.fin, errors='coerce'); legs['mor'] = pd.to_numeric(legs.mor, errors='coerce')
LM = {(r.race_key, r.combination): (r.fin, r.mor) for r in legs.itertuples()}
two = pd.read_pickle('two.pkl'); two = two[(two['mode'] == 'live') & (two.n_entries == 7)].set_index('race_key')

def st(o):
    w = (1 / o) / (1 / o).sum(); return np.maximum(100, np.floor(w * 100) * 100)

tp = pd.read_csv('trio_pred.csv', sep='|', header=None, names=['race_key', 'plan', 'legs'])
tp = tp.drop_duplicates(['race_key', 'plan'], keep='last')
rows = []
for r in tp.itertuples():
    if r.race_key not in two.index: continue
    t = two.loc[r.race_key]
    win = '-'.join(map(str, sorted([int(t.f1), int(t.f2), int(t.f3)])))
    L = json.loads(r.legs)
    cs = ['-'.join(map(str, sorted(int(x) for x in d['combo'].split('=')))) for d in L]
    pr = np.array([d['pred_odds'] for d in L], float)
    fm = [LM.get((r.race_key, c)) for c in cs]
    if any(v is None for v in fm): continue
    fin = np.array([v[0] for v in fm]); mor = np.array([v[1] for v in fm])
    if (fin >= 9999).any() or np.isnan(mor).any(): continue
    hit = np.array([c == win for c in cs])
    out = {'race_key': r.race_key, 'plan': r.plan, 'k': len(cs)}
    for nm, o in [('pred', pr), ('mor', mor)]:
        s = st(o); out[nm] = (s[hit] * fin[hit]).sum() / s.sum()
        out['mult_' + nm] = 1 / (1 / o).sum()
    out['minpred'] = pr.min()
    rows.append(out)
R = pd.DataFrame(rows)
print('== 同じ買い目で 予測オッズ dutch ↔ 朝オッズ dutch')
for p, g in R.groupby('plan'):
    d = g.pred - g.mor; se = d.std() / np.sqrt(len(g))
    print(f'{p:7s} n={len(g)} 表示的中 予測 {100*(g.pred>1).mean():.1f}% / 朝 {100*(g.mor>1).mean():.1f}%  '
          f'回収 予測 {100*g.pred.mean():.1f} / 朝 {100*g.mor.mean():.1f}  差 [{100*(d.mean()-1.96*se):+.1f},{100*(d.mean()+1.96*se):+.1f}]  '
          f'合成倍率 中央 予測 {g.mult_pred.median():.2f} / 朝 {g.mult_mor.median():.2f}')

print('\n== 型A/B/C 二軸＋相手3点（朝オッズ）で既定ゲートが切る割合')
x = []
for rk, t in two[two.type_label.isin(['A', 'B', 'C'])].iterrows():
    a, b = int(t.axis1), int(t.axis2)
    others = [int(v) for v in str(t.p3_order).split('-') if int(v) not in (a, b)][:3]
    cs = ['-'.join(map(str, sorted([a, b, c]))) for c in others]
    fm = [LM.get((rk, c)) for c in cs]
    if any(v is None for v in fm): continue
    mor = np.array([v[1] for v in fm])
    if np.isnan(mor).any(): continue
    x.append({'mult': 1 / (1 / mor).sum(), 'minodds': mor.min()})
X = pd.DataFrame(x)
print(f'n={len(X)}  合成倍率 中央 {X.mult.median():.2f}  <=2.0倍(平均2万ゲート) {100*(X.mult<=2).mean():.0f}%  '
      f'最低目<2.0倍 {100*(X.minodds<2).mean():.0f}%  合成<1.0倍 {100*(X.mult<1).mean():.0f}%  どちらかで落ちる {100*((X.mult<=2)|(X.minodds<2)).mean():.0f}%')
