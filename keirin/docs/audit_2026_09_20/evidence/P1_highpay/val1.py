import sys, pandas as pd, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, picks
from src import type_lab as TL
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
p=pd.read_csv(f"{AUD}/B_live/tlp_paper.csv")
p=p[p["mode"]=="paper"]
print("rule_version:", p.rule_version.value_counts().to_dict())
print("現行 rule_version:", TL.rule_version(7))
races=lib.get_races()
R={r["key"]:r for r in races}
for pk in ["A_sign","B_sign","C_sign","D_sign","E_sign","F_sign","A_ana"]:
    sub=p[p.plan_key==pk]
    sub=sub[sub.race_key.isin(R)]
    if len(sub)==0: continue
    sub=sub.sample(min(400,len(sub)), random_state=1)
    nl_ok=pm_ok=tot=0; dif=[]
    for _,row in sub.iterrows():
        r=R[row.race_key]
        got=picks.build_pick(r, TL.PLANS[pk])
        tot+=1
        if got is None: continue
        legs,stakes,pu=got
        odds=r["tpo"] if pu.bet_type=="trio" else r["po"]
        if len(legs)==int(row.n_legs): nl_ok+=1
        pm=TL.mean_expected_payout(stakes,odds)
        dif.append(pm-float(row.pred_mean_payout))
        if abs(pm-float(row.pred_mean_payout))<1.0: pm_ok+=1
    print(f"{pk}: n={tot} n_legs一致 {nl_ok} pred_mean一致 {pm_ok} 中央差 {np.median(dif) if dif else float('nan'):.1f}")
