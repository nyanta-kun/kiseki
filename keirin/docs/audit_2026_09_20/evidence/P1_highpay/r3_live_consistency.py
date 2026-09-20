"""実売の高額・看板枠 ROI 44% は、板の水準（70〜80%）から出うるか？"""
import sys, json, numpy as np, pandas as pd, collections
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, engine as E
from src import type_lab as TL
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
s=pd.read_csv(f"{AUD}/B_live/subs.csv")
s=s[s.deleted_at.isna() & s.status.isin(["published","submitted"]) & s.settled_at.notna()]
s=s[s.settled_bet>0].copy(); s["settled_hit"]=s.settled_hit.map({"t":True,"f":False,True:True,False:False})
print("実売 母集団", len(s), "ROI %.2f%%"%(100*s.settled_payout.sum()/s.settled_bet.sum()))
HP_TL={"B_sign","C_sign","D_sign","B_big","C_big","D_big","F_sign","T_upset"}
HP_OLD={"7T1","7T3","7H1","7H2","9H1","7M1"}
tl=s[s.rank_key.isin(HP_TL)]; old=s[s.rank_key.isin(HP_OLD)]
for nm,d in [("型ラボ高額枠",tl),("旧ランク高額",old),("両方",s[s.rank_key.isin(HP_TL|HP_OLD)]),
             ("A_ana",s[s.rank_key=="A_ana"]),("F_pay",s[s.rank_key=="F_pay"])]:
    if len(d)==0: continue
    print(f"{nm}: n={len(d)} bet={d.settled_bet.sum():,} pay={d.settled_payout.sum():,} "
          f"ROI {100*d.settled_payout.sum()/d.settled_bet.sum():.2f}% 的中 {100*d.settled_hit.mean():.2f}%")
print("型ラボ高額枠の内訳:"); print(tl.groupby("rank_key").agg(n=("settled_bet","size"),
      bet=("settled_bet","sum"), pay=("settled_payout","sum"), hit=("settled_hit","sum")))

# ---- 板からのシミュレーション（同じプラン構成・同じ件数） ----
races=lib.get_races(); conf=lib.window(races, lib.CONFIRM); expl=lib.window(races, lib.EXPLORE)
pool={}
for w,nm in [(conf,"confirm"),(expl,"explore")]:
    for pk in ["B_sign","C_sign","D_sign","B_big","C_big","D_big","F_sign"]:
        rows=E.run_plan(w, TL.PLANS[pk])
        pool[(nm,pk)]=np.array([x["payout"]/x["bet"] for x in rows])
mix=tl.rank_key.value_counts().to_dict()
print("\nシミュレーション（板の同プラン分布から復元抽出・T_upset は板に無いので除外）")
for nm in ["confirm","explore"]:
    rng=np.random.default_rng(7); NB=20000; out=np.empty(NB)
    for b in range(NB):
        tot=0.0; cnt=0
        for pk,c in mix.items():
            p=pool.get((nm,pk))
            if p is None or len(p)==0: continue
            tot+=rng.choice(p,c).sum(); cnt+=c
        out[b]=tot/cnt
    sub=tl[tl.rank_key.isin([k for k in mix if (nm,k) in pool and len(pool[(nm,k)])>0])]
    obs=sub.settled_payout.sum()/sub.settled_bet.sum()
    print(f"  板{nm}: 期待ROI {out.mean()*100:.2f}%  実売同構成(n={len(sub)}) ROI {obs*100:.2f}%  "
          f"P(sim<=obs)={np.mean(out<=obs):.4f}  5%点 {np.percentile(out,5)*100:.1f}%  "
          f"25%点 {np.percentile(out,25)*100:.1f}%")
