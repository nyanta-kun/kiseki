"""① レース単位 bootstrap CI が低的中率商品で信用できるか ② 実売(旧ランク含む)の帯構成からの期待ROI"""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
from src import type_lab as TL
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
races=lib.get_races(); conf=lib.window(races,lib.CONFIRM)
def bcd(r): return r["type"] in ("B","C","D")
rows=design.build(conf,150_000,pop=bcd)
pool=np.array([x["payout"]/x["bet"] for x in rows]); true=pool.mean()
print(f"① 母集団の真の ROI = {true*100:.2f}%（板・確認窓・T=15万・n={len(pool)}）")
TRUE_TARGET=0.775
pool = pool*(TRUE_TARGET/true)   # 真値を板のプラン構成期待 77.5% に合わせる
true = pool.mean()
print(f"   （検定用に真値を {true*100:.1f}% へ縮めて再検査）")
rng=np.random.default_rng(5); NS=2000; cnt_excl=0; cov=0; los=[];his=[]
for i in range(NS):
    samp=rng.choice(pool,396)
    b=np.ones(396)*10000; p=samp*10000
    bs=np.empty(1000)
    idx=rng.integers(0,396,(1000,396))
    for j in range(1000):
        k=idx[j]; bs[j]=p[k].sum()/b[k].sum()
    lo,hi=np.percentile(bs,[2.5,97.5]); los.append(lo); his.append(hi)
    if hi<0.75: cnt_excl+=1
    if lo<=true<=hi: cov+=1
print(f"   n=396 を真値 {true*100:.1f}% から引いて、レース単位 bootstrap 95%CI が")
print(f"   ・真値を含む割合（本来95%）        : {cov/NS*100:.1f}%")
print(f"   ・『CI上限 < 75%』と誤判定する割合 : {cnt_excl/NS*100:.1f}%")
print(f"   ・CI の中央 [{np.median(los)*100:.1f}, {np.median(his)*100:.1f}]")

print("\n② 実売の高額枠を『買い目の予測オッズ帯』で評価（旧ランクを含む）")
BND=[(0,10),(10,15),(15,30),(30,60),(60,100),(100,200),(200,300),(300,600),(600,1e9)]
def bi(o):
    for j,(lo,hi) in enumerate(BND):
        if lo<=o<hi: return j
    return len(BND)-1
num=np.zeros(len(BND)); den=np.zeros(len(BND))
for r in conf:
    win=r["win"]; pay=r["pay"]/100.0
    if win is None: continue
    for k,o in r["po"].items():
        j=bi(float(o)); den[j]+=1
        if k==win: num[j]+=pay
bb=num/np.maximum(den,1)
s=pd.read_csv(f"{AUD}/B_live/subs.csv")
s=s[s.deleted_at.isna() & s.status.isin(["published","submitted"]) & s.settled_at.notna()]
s=s[(s.settled_bet>0) & s.bet_detail.notna()].copy()
HP_TL={"B_sign","C_sign","D_sign","B_big","C_big","D_big","F_sign","T_upset"}
HP_OLD={"7T1","7T3","7H1","7H2","9H1","7M1"}
for nm,keys in [("型ラボ高額枠",HP_TL),("旧ランク高額",HP_OLD),("A_ana",{"A_ana"}),
                ("本線(型ラボ *_hit)",{"A_hit","B_hit","C_hit","D_hit","E_hit","F_hit"})]:
    d=s[s.rank_key.isin(keys)]
    w=np.zeros(len(BND)); tb=0
    for bd in d.bet_detail:
        for lg in (json.loads(bd).get("lines") or []):
            o=float(lg.get("odds") or 0); st=int(lg.get("stake") or 0)
            if o<=0 or st<=0: continue
            w[bi(o)]+=st; tb+=st
    exp=float((w*bb).sum()/w.sum()) if w.sum() else float("nan")
    roi=d.settled_payout.sum()/d.settled_bet.sum()
    comp=" ".join(f"{BND[j][0]}-{int(BND[j][1]) if BND[j][1]<1e8 else 0}:{w[j]/w.sum()*100:.0f}%"
                  for j in range(len(BND)) if w.sum() and w[j]/w.sum()>=0.04)
    print(f"  {nm:<18} n={len(d):>4} 実ROI {roi*100:>6.2f}%  帯からの無情報期待 {exp*100:>6.2f}%  帯構成 {comp}")
