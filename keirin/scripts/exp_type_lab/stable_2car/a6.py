"""二軸を三連複・三連単で買う方法の比較（7車・live・8/27〜）。
1レース1万円。均等は 10,000÷点数（100円切り捨て）。ダッチは朝オッズの逆数に比例（100円単位・最低100円）。
払戻は確定オッズ × 当たり目の賭け金。結果は arms.pkl（レース×買い方の倍率）に保存。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
two = pd.read_pickle('two.pkl')
two = two[(two['mode'] == 'live') & (two.n_entries == 7)].copy().set_index('race_key')
legs = pd.read_csv('legs3.csv', dtype={'combination': str})
legs = legs[legs.race_key.str.match(r'^\d{8}_')]
legs['fin'] = pd.to_numeric(legs.fin, errors='coerce'); legs['mor'] = pd.to_numeric(legs.mor, errors='coerce')
L = {k: g for k, g in legs.groupby('race_key')}
BUD = 10000

def stakes(odds_m, dutch):
    n = len(odds_m)
    if n == 0: return np.array([])
    if not dutch or np.any(~np.isfinite(odds_m)) or np.any(odds_m <= 0):
        return np.full(n, (BUD // n) // 100 * 100, dtype=float)
    w = (1 / odds_m) / (1 / odds_m).sum()
    s = np.maximum(100, np.floor(w * BUD / 100) * 100)
    return s

def ret(sel, win, dutch):
    """sel: DataFrame の行（買う目）。win: 当たり目の combination。倍率（払戻/賭け金）を返す"""
    if len(sel) == 0: return np.nan
    s = stakes(sel.mor.values, dutch)
    hit = sel.combination.values == win
    return float((s[hit] * sel.fin.values[hit]).sum() / s.sum())

rows = []
for rk, r in two.iterrows():
    g = L.get(rk)
    if g is None or (g.fin >= 9999).any(): continue
    a, b = int(r.axis1), int(r.axis2)
    order = [int(x) for x in str(r.p3_order).split('-')]
    others = [x for x in order if x not in (a, b)]  # 指数(3着内率)順の相手
    f1, f2, f3 = int(r.f1), int(r.f2), int(r.f3)
    trio_win = '-'.join(map(str, sorted([f1, f2, f3]))); tf_win = f'{f1}-{f2}-{f3}'
    tr = g[g.bet_type == 'trio'].copy(); tf = g[g.bet_type == 'trifecta'].copy()
    tr['x'] = tr.combination.map(lambda c: [int(v) for v in c.split('-') if int(v) not in (a, b)][0])
    p = tf.combination.str.split('-', expand=True).astype(int); tf['p1'], tf['p2'], tf['p3'] = p[0].values, p[1].values, p[2].values
    out = {'race_key': rk}
    for k in [5, 4, 3, 2]:
        part = set(others[:k]); s = tr[tr.x.isin(part)]
        out[f'trio_{k}_eq'] = ret(s, trio_win, False); out[f'trio_{k}_du'] = ret(s, trio_win, True)
        m = tf[(((tf.p1 == a) & (tf.p2 == b)) | ((tf.p1 == b) & (tf.p2 == a))) & tf.p3.isin(part)]
        out[f'tfM_{k}_eq'] = ret(m, tf_win, False); out[f'tfM_{k}_du'] = ret(m, tf_win, True)
        f = tf[(tf.p1 == a) & (tf.p2 == b) & tf.p3.isin(part)]
        out[f'tfF_{k}_eq'] = ret(f, tf_win, False); out[f'tfF_{k}_du'] = ret(f, tf_win, True)
    # 二軸が3着以内なら当たり（三連単30点ダッチ＝ワイド相当）
    out['tfW_30_du'] = ret(tf, tf_win, True)
    rows.append(out)
A = pd.DataFrame(rows).set_index('race_key')
A = A.join(two[['race_date', 'wk', 'type_label', 'axis_sum', 'r_q']])
A.to_pickle('arms.pkl')
print(len(A))
