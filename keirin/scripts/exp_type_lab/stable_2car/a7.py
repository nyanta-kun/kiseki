"""買い方ごとの 表示的中・回収・週の揺れ（型グループ別・前後半）。"""
import os, numpy as np, pandas as pd
os.chdir(os.path.dirname(os.path.abspath(__file__)))
A = pd.read_pickle('arms.pkl')
A['half'] = np.where(A.race_date < '2026-10-10', 'H1', 'H2')
ARMS = ['r_q', 'trio_5_eq', 'trio_5_du', 'trio_4_du', 'trio_3_du', 'trio_2_du', 'trio_3_eq',
        'tfM_5_du', 'tfM_4_du', 'tfM_3_du', 'tfM_2_du', 'tfF_5_du', 'tfF_3_du', 'tfW_30_du']
NAME = {'r_q': '参考 2車複1点', 'trio_5_eq': '三連複 総流し5点 均等', 'trio_5_du': '三連複 総流し5点 ダッチ',
        'trio_4_du': '三連複 相手4点 ダッチ', 'trio_3_du': '三連複 相手3点 ダッチ', 'trio_2_du': '三連複 相手2点 ダッチ',
        'trio_3_eq': '三連複 相手3点 均等', 'tfM_5_du': '三連単 1-2着マルチ×総流し10点 ダッチ',
        'tfM_4_du': '三連単 マルチ×相手4 (8点) ダッチ', 'tfM_3_du': '三連単 マルチ×相手3 (6点) ダッチ',
        'tfM_2_du': '三連単 マルチ×相手2 (4点) ダッチ', 'tfF_5_du': '三連単 軸1→軸2→総流し5点 ダッチ',
        'tfF_3_du': '三連単 軸1→軸2→相手3 ダッチ', 'tfW_30_du': '三連単 二軸3着内30点 ダッチ'}

def s(x, k):
    v = x[k].dropna(); se = v.std() / np.sqrt(len(v))
    wk = x.groupby('wk')[k].agg(['size', 'mean', lambda t: (t > 1).mean()]); wk = wk[wk['size'] >= 40]
    h = x.groupby('half')[k].agg(['mean', lambda t: (t > 1).mean()])
    return [NAME[k], len(v), round(100 * (v > 1).mean(), 1), round(100 * v.mean(), 1),
            f'[{100*(v.mean()-1.96*se):.0f},{100*(v.mean()+1.96*se):.0f}]',
            f"{100*h.iloc[0,1]:.0f}/{100*h.iloc[1,1]:.0f}", f"{100*h.iloc[0,0]:.0f}/{100*h.iloc[1,0]:.0f}",
            round(100 * wk['mean'].std(), 1), round(100 * wk['mean'].min()), round(100 * wk.iloc[:, 2].min())]

pd.set_option('display.width', 280); pd.set_option('display.max_colwidth', 40)
COLS = ['買い方', 'R', '表示的中', '回収', '95%CI', '的中 前/後', '回収 前/後', '週回収SD', '週回収最低', '週的中最低']
for nm, m in [('型A/B/C', A.type_label.isin(['A', 'B', 'C'])), ('型D', A.type_label == 'D'),
              ('型E', A.type_label == 'E'), ('型F', A.type_label == 'F')]:
    print(f'===== {nm}')
    print(pd.DataFrame([s(A[m], k) for k in ARMS], columns=COLS).to_string(index=False))
