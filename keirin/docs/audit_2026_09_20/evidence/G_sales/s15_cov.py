import pandas as pd, numpy as np, sys
sys.path.insert(0,'/Users/ysuzuki/GitHub/kiseki/backend/src')
from services.keirin_marquee import is_marquee_race
pd.set_option('display.width',250); pd.set_option('display.max_rows',100)
c=pd.read_csv('cov.csv')
c=c[c['cancel']==0].copy()
c['covered']=c['covered'].astype(str).str.lower().isin(['t','true'])
def rtg(rt):
    if not isinstance(rt,str): return 'other'
    if '準決勝' in rt: return 'semifinal'
    if '決勝' in rt: return 'final'
    if '特選' in rt or '選抜' in rt or '特秀' in rt or '優秀' in rt: return 'tokusen'
    if '予選' in rt: return 'yosen'
    if '一般' in rt: return 'ippan'
    return 'other'
c['rtg']=c['race_type'].apply(rtg)
c['marquee']=c['race_type'].apply(lambda x: is_marquee_race(x if isinstance(x,str) else None))
print("開催レース総数(中止除く):",len(c)," 商品あり:",int(c['covered'].sum()),f"({c['covered'].mean():.1%})")
print("\n種別別カバー率と平均売上:")
print(c.groupby('rtg').agg(n=('covered','size'),covered=('covered','sum'),rate=('covered','mean'),
   paid_cov=('paid',lambda s: s[s>0].mean() if (s>0).any() else 0)).round(3).to_string())
print("\n車数別:")
print(c.groupby('n_entries').agg(n=('covered','size'),rate=('covered','mean')).round(3).to_string())
print("\n決勝の未カバー:", int(((c['rtg']=='final')&(~c['covered'])).sum()), "/", int((c['rtg']=='final').sum()))
u=c[(c['rtg']=='final')&(~c['covered'])]
print(u.groupby('n_entries').size().to_dict())
print("\n看板(定義)カバー率:", round(c[c['marquee']]['covered'].mean(),3), " 非看板:", round(c[~c['marquee']]['covered'].mean(),3))
# 潜在売上の試算: 決勝を全部カバーしたら
cov=pd.read_csv('races.csv'); 
