import sys, pandas as pd, numpy as np, collections
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, picks
from src import type_lab as TL
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
p=pd.read_csv(f"{AUD}/B_live/tlp_paper.csv"); p=p[p["mode"]=="paper"]
races=lib.get_races(); R={r["key"]:r for r in races}
sub=p[p.plan_key=="C_sign"]; sub=sub[sub.race_key.isin(R)].sample(400, random_state=1)
dn=collections.Counter(); dpm=[]
for _,row in sub.iterrows():
    r=R[row.race_key]
    # 素の build_legs + allocate（osae/line_swap/order_swap 抜き）
    pl=TL.PLANS["C_sign"]
    legs=TL.build_legs(r["shape"], pl, r["po"], r["probs"])
    if not legs: continue
    st=TL.allocate(legs, r["po"], r["probs"], pl)
    if not st: continue
    legs=[c for c in legs if st.get(c,0)>0]
    dn[len(legs)-int(row.n_legs)]+=1
    dpm.append(TL.mean_expected_payout(st,r["po"])-float(row.pred_mean_payout))
print("素組み: n_legs差", dict(sorted(dn.items())))
print("pred_mean差 中央 %.1f p05 %.1f p95 %.1f 一致率 %.3f"%(np.median(dpm),np.percentile(dpm,5),np.percentile(dpm,95),np.mean(np.abs(dpm)<1)))
