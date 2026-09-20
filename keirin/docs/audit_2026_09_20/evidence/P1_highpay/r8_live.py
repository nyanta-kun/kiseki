"""実売の高額枠 ROI 44% が板の水準と整合するかを、期待的中本数で検定する。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
from src import type_lab as TL
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
s=pd.read_csv(f"{AUD}/B_live/subs.csv")
s=s[s.deleted_at.isna() & s.status.isin(["published","submitted"]) & s.settled_at.notna()]
s=s[s.settled_bet>0].copy()
s["hit"]=s.settled_hit.map({"t":True,"f":False})
races=lib.get_races()
conf=lib.window(races,lib.CONFIRM); expl=lib.window(races,lib.EXPLORE)
# 板から各プランの (ROI分布) を作る
def pool_for(ws, pk):
    if pk=="T_upset":
        rows=E.run_plan(ws, TL.PLANS["T_upset"])   # type_label 'T' はどの型にも掛かる
        if not rows:
            out=[]
            for r in ws:
                got=TL.build_with_gate_fallback(r["shape"],TL.PLANS["T_upset"],r["po"],r["probs"],7)
                if not got: continue
                legs,st,pu=got
                if not E.gate_pass(legs,st,r["po"]): continue
                out.append(E.evaluate(r,legs,st,"trifecta",r["po"]))
            rows=out
        return np.array([x["payout"]/x["bet"] for x in rows])
    rows=E.run_plan(ws, TL.PLANS[pk])
    return np.array([x["payout"]/x["bet"] for x in rows])
HP=["B_sign","C_sign","D_sign","B_big","C_big","D_big","F_sign","T_upset"]
pools={}
for nm,ws in [("確認",conf),("探索",expl)]:
    for pk in HP:
        pools[(nm,pk)]=pool_for(ws,pk)
tl=s[s.rank_key.isin(HP)]
mix=tl.rank_key.value_counts().to_dict()
print("実売 型ラボ高額枠 n=%d ROI %.2f%%"%(len(tl),100*tl.settled_payout.sum()/tl.settled_bet.sum()))
print(f"{'plan':<9}{'実売n':>6}{'実売的中':>8}{'板の的中率':>10}{'期待的中本数':>12}{'板ROI%':>8}")
for pk,c in sorted(mix.items(), key=lambda x:-x[1]):
    p=pools[("確認",pk)]
    if len(p)==0: print(f"{pk:<9}{c:>6}  板なし"); continue
    hr=float(np.mean(p>0))
    print(f"{pk:<9}{c:>6}{int(tl[tl.rank_key==pk].hit.sum()):>8}{hr*100:>9.2f}%{hr*c:>12.2f}{p.mean()*100:>8.2f}")
for nm in ["確認","探索"]:
    rng=np.random.default_rng(11); NB=40000; out=np.empty(NB)
    use={k:v for k,v in mix.items() if len(pools[(nm,k)])>0}
    for b in range(NB):
        tot=0.0; cnt=0
        for pk,c in use.items():
            tot+=rng.choice(pools[(nm,pk)],c).sum(); cnt+=c
        out[b]=tot/cnt
    sub=tl[tl.rank_key.isin(use)]
    obs=sub.settled_payout.sum()/sub.settled_bet.sum()
    print(f"\n板{nm}を真値としたときの分布（同じプラン構成・n={len(sub)}）:")
    print(f"  期待ROI {out.mean()*100:.2f}%  実売 {obs*100:.2f}%  P(sim<=実売)={np.mean(out<=obs):.4f}")
    print(f"  5%点 {np.percentile(out,5)*100:.1f}% / 25%点 {np.percentile(out,25)*100:.1f}% / "
          f"中央 {np.percentile(out,50)*100:.1f}% / 95%点 {np.percentile(out,95)*100:.1f}%")
# 本線でも同じ検定（方法の健全性チェック）
MAIN=["A_hit","B_hit","C_hit","D_hit","E_hit","F_hit"]
for pk in MAIN: pools[("確認",pk)]=pool_for(conf,pk)
ml=s[s.rank_key.isin(MAIN)]; mmix=ml.rank_key.value_counts().to_dict()
rng=np.random.default_rng(3); out=np.empty(20000)
for b in range(20000):
    tot=0.0;cnt=0
    for pk,c in mmix.items():
        tot+=rng.choice(pools[("確認",pk)],c).sum(); cnt+=c
    out[b]=tot/cnt
obs=ml.settled_payout.sum()/ml.settled_bet.sum()
print(f"\n[方法の健全性] 本線 n={len(ml)}: 板期待 {out.mean()*100:.2f}% / 実売 {obs*100:.2f}% "
      f"P(sim<=実売)={np.mean(out<=obs):.4f}")
