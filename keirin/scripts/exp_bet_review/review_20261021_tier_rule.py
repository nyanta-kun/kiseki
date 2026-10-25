"""2026-10-21 検証 §2: 7車を「段」（販売停止中の T_*）で売っていたらどうだったかの組み直し。

本番の段の割り当て（A_ana → 固め/広め/手広く/荒れ）と入稿ゲート（段は悪い側の見込み1.5万円）を
本番の関数・定数で当てる。日次上限・軸信頼ゲートは段には掛からない（2026-09-15 の規則）。
並び未公開による見送りは再現しない。

使い方（keirin/ で）: .venv/bin/python scripts/exp_bet_review/review_20261021_tier_rule.py PKLDIR 2026-09-16 2026-10-20
"""
import sys, pandas as pd, numpy as np
sys.path.insert(0,'.')
from src.type_lab import (tier_realized_min_payout, TIER_POINT_PAYOUT_MIN, TIER_AXIS_FIRM_MIN,
    TIER_AXIS_MID_MIN, ANA_PW_ENT_MIN, TIER_POINT_GATE_PLANS)
from src.stake_allocation import MIN_POINT_ODDS, MIN_MEAN_PAYOUT
S=sys.argv[1]; D0,D1=sys.argv[2],sys.argv[3]
d=pd.read_pickle(f"{S}/live7_legs.pkl"); d['day']=d.race_date.astype(str)
d=d[(d.day>=D0)&(d.day<=D1)]
Sb=pd.read_pickle(f"{S}/subs.pkl"); Sb=Sb[Sb.status=='published']
def gate(r):
    legs=r.legs or []
    if r.plan_key in TIER_POINT_GATE_PLANS:
        w=tier_realized_min_payout(legs, r.race_type, float(r.axis_sum) if r.axis_sum is not None else None)
        if w is not None and w<TIER_POINT_PAYOUT_MIN: return False
    else:
        if r.pred_mean_payout is not None and float(r.pred_mean_payout)<=MIN_MEAN_PAYOUT: return False
    o=[float(l.get('pred_odds') or 0) for l in legs]; o=[x for x in o if x>0]
    if o and min(o)<MIN_POINT_ODDS: return False
    return True
by={(r.race_key,r.plan_key):r for r in d.itertuples()}
races=d.drop_duplicates('race_key')[['race_key','day','type_label','axis_sum','pw_ent','race_type']]
choice=[]
for r in races.itertuples():
    a=float(r.axis_sum) if r.axis_sum is not None else None
    if a is None: choice.append(None); continue
    if r.type_label=='A' and r.pw_ent is not None and float(r.pw_ent)>=ANA_PW_ENT_MIN: pk='A_ana'
    elif a>TIER_AXIS_FIRM_MIN: pk='T_firm'
    elif a>TIER_AXIS_MID_MIN: pk='T_mid'
    else:
        ax=by.get((r.race_key,'T_axis'))
        pk='T_axis' if (ax is not None and gate(ax)) else 'T_upset'
    row=by.get((r.race_key,pk))
    choice.append(pk if (row is not None and gate(row)) else None)
races['tier_pk']=choice
t=races.dropna(subset=['tier_pk']).merge(d[['race_key','plan_key','settled','payout','budget']],left_on=['race_key','tier_pk'],right_on=['race_key','plan_key'])
t=t[t.settled]
sold=Sb.merge(d[['race_key','plan_key','day','settled','payout','budget']],left_on=['race_key','rank_key'],right_on=['race_key','plan_key'])
sold=sold[sold.settled]
def summ(df,name):
    p=df.payout.values; b=df.budget.sum(); s=np.sort(p)[::-1]
    g=df.groupby('day').agg(pay=('payout','sum'),bet=('budget','sum'),n=('payout','size'))
    g['roi']=100*g.pay/g.bet
    # weekly
    dd=pd.to_datetime(g.index); g['wk']=dd.to_period('W-SUN')
    w=g.groupby('wk')[['pay','bet']].sum(); wr=100*w.pay/w.bet
    print(f"{name:22s} n={len(df):5d} /day={len(df)/g.shape[0]:5.1f} ROI={100*p.sum()/b:5.1f} ex1={100*s[1:].sum()/(b-1e4):5.1f} ex3={100*s[3:].sum()/(b-3e4):5.1f} hit={100*(p>0).mean():4.1f}% 10万+/day={(p>=1e5).sum()/g.shape[0]:.2f} 4万+/day={(p>=4e4).sum()/g.shape[0]:.2f} | day ROI p10={np.percentile(g.roi,10):5.1f} med={g.roi.median():5.1f} <50%days={100*(g.roi<50).mean():4.1f}% | wk ROI min={wr.min():5.1f} max={wr.max():5.1f}")
    return g
print(f"window {D0}..{D1}")
print("tier picks:",t.tier_pk.value_counts().to_dict())
g1=summ(sold[sold.n_entries==7] if 'n_entries' in sold else sold,"ACTUAL sold 7car")
g2=summ(t,"TIER rule 7car")
# same races only
j=sold.merge(t[['race_key','payout']],on='race_key',suffixes=('','_t'))
print(f"overlap races={len(j)} sold ROI={100*j.payout.sum()/(1e4*len(j)):.1f} tier ROI={100*j.payout_t.sum()/(1e4*len(j)):.1f}")
gg=g1[['pay','bet']].join(g2[['pay','bet']],lsuffix='_s',rsuffix='_t',how='inner')
rng=np.random.default_rng(1); bs=[]
for _ in range(2000):
    k=rng.integers(len(gg),size=len(gg)); x=gg.iloc[k]
    bs.append(100*x.pay_t.sum()/x.bet_t.sum()-100*x.pay_s.sum()/x.bet_s.sum())
print(f"ΔROI tier-actual (day bootstrap) {np.mean(bs):+.1f} [{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}]")
pd.concat({'actual':g1.roi,'tier':g2.roi},axis=1).to_csv(f"{S}/daily_{D0}_{D1}.csv")
