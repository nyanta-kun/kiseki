"""2026-10-21 検証 §4.1・§4.2: 段の組み直しの検算（09-15 の実売と突き合わせ）と、差の出どころの分解。

使い方（keirin/ で）: .venv/bin/python scripts/exp_bet_review/review_20261021_tier_check.py PKLDIR
"""
import sys, runpy, pandas as pd, numpy as np
S=sys.argv[1]
# 1. 9/15 validation: rerun tier rule for that day and compare to actual tier sales
sys.argv=['x',S,'2026-09-15','2026-09-15']
import io, contextlib
buf=io.StringIO()
with contextlib.redirect_stdout(buf): ns=runpy.run_path('scripts/exp_bet_review/review_20261021_tier_rule.py')
races=ns['races']; Sb=pd.read_pickle(f"{S}/subs.pkl"); Sb=Sb[Sb.status=='published']
act=Sb[Sb.race_key.str.startswith('20260915')][['race_key','rank_key']]
j=races.merge(act,on='race_key',how='outer')
tier_act=j[j.rank_key.isin(['T_firm','T_mid','T_upset','T_axis','A_ana'])]
print("9/15 actual tier/A_ana sales:",len(tier_act))
print(" assignment match (recon pick == sold):",(tier_act.tier_pk==tier_act.rank_key).sum(),
      "| recon gated out:",tier_act.tier_pk.isna().sum())
print(pd.crosstab(tier_act.rank_key, tier_act.tier_pk.fillna('GATED')))
# assignment before gate: recompute tier by axis only
# 2. decompose overlap by actual product
sys.argv=['x',S,'2026-09-16','2026-10-20']
with contextlib.redirect_stdout(io.StringIO()): ns=runpy.run_path('scripts/exp_bet_review/review_20261021_tier_rule.py')
sold=ns['sold']; t=ns['t']
jj=sold.merge(t[['race_key','payout','tier_pk']],on='race_key',suffixes=('','_t'))
jj['grp']=np.where(jj.rank_key=='L_lead','L_lead',np.where(jj.origin=='highpay_fill','highpay','rank'))
rng=np.random.default_rng(2)
for gname,df in list(jj.groupby('grp'))+[('ALL',jj)]:
    dd=df.groupby('day')[['payout','payout_t']].sum(); n=df.groupby('day').size()
    bs=[]
    for _ in range(2000):
        k=rng.integers(len(dd),size=len(dd)); bs.append(100*(dd.payout_t.values[k].sum()-dd.payout.values[k].sum())/(1e4*n.values[k].sum()))
    print(f"{gname:8s} n={len(df):4d} sold={100*df.payout.sum()/(1e4*len(df)):5.1f} tier={100*df.payout_t.sum()/(1e4*len(df)):5.1f} d={np.mean(bs):+.1f} [{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}]")
# races sold by actual but not by tier, and vice versa
a=set(sold.race_key); b=set(t.race_key)
x=sold[~sold.race_key.isin(b)]; y=t[~t.race_key.isin(a)]
print("actual-only races",len(x),"ROI",round(100*x.payout.sum()/(1e4*len(x)),1),"| tier-only races",len(y),"ROI",round(100*y.payout.sum()/(1e4*len(y)),1))
