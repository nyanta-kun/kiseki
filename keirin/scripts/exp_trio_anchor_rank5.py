#!/usr/bin/env python3
"""順位5を起点に「同程度の確からしさ」の相手まで買う（ユーザー規則の忠実版）+ 月次安定性。

🔴 **結果: 成立しない。** 順位5と同程度の確からしさを持つのは順位3・順位4で、
   足すと実測で最悪の順位4（71.5%）を必ず引き込む（ROI 83.6%→76.7%）。
   この母集団では**最良の1点と最悪の1点が同じ確からしさ帯にいる**。

🟢 順位5の優位は月次でも安定（**8ヶ月すべてで壁の上**・月次最小 75.9%）。
   他の順位は全て複数月で壁を割る。最高月が最多なのではなく**下振れしない**のが特徴。
"""
import sys, json, os, itertools, statistics as st, numpy as np, psycopg2
from collections import defaultdict
sys.path.insert(0,'.')
from scripts.exp_leg_prob_heads import strengths
from src.strategy_wt import unit_stake
np.random.seed(271); PAY=0.7485
rows=[]
with open("data/exp/tf_shape_cache4.jsonl") as f:
    for line in f:
        r=json.loads(line)
        if r.get("win"): rows.append(r)
con=psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur=con.cursor()
keys=[r["race_key"] for r in rows]; board=defaultdict(dict)
for i in range(0,len(keys),2000):
    cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                "where bet_type='trio' and race_key=any(%s) and odds_value>0",(keys[i:i+2000],))
    for rk,c,o in cur.fetchall():
        s=frozenset(int(x) for x in str(c).replace("=","-").split("-"))
        if len(s)==3: board[rk][s]=float(o)
def tprobs(pw,p3):
    s=[strengths(pw,p3,a) for a in (1.0,.5,0.0)]; s1=sum(s[0].values()); cars=list(pw)
    out=defaultdict(float)
    for x,y,z in itertools.permutations(cars,3):
        d2=sum(s[1][c] for c in cars if c!=x); d3=sum(s[2][c] for c in cars if c not in (x,y))
        if d2<=0 or d3<=0: continue
        out[frozenset((x,y,z))]+=(s[0][x]/s1)*(s[1][y]/d2)*(s[2][z]/d3)
    return out
R=[]
for r in rows:
    bd=board.get(r["race_key"])
    if not bd: continue
    p3={int(k):v for k,v in r["p3"].items()}; pw={int(k):v for k,v in r["pw"].items()}
    if len(p3)<7 or len(pw)<7: continue
    o=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]
    a1,a2=o[0],o[1]; tp=tprobs(pw,p3)
    base=frozenset((a1,a2,o[4]))
    if base not in bd: continue
    cands=[]
    for i,c in enumerate(o[2:],start=3):
        k=frozenset((a1,a2,c))
        if k in bd: cands.append(dict(key=k,prob=tp.get(k,0.0),rank=i))
    R.append(dict(date=r["race_date"],bd=bd,base=base,base_p=tp.get(base,0.0),
                  cands=cands,wins={frozenset(int(x) for x in w.split("-")) for w in r["win"]}))
print(f"{len(R):,}R\n順位5を起点に、確からしさが基準の α 倍以上の相手を追加する\n")
print(f"{'α':>6}{'窓':>5}{'平均点':>7}{'的中%':>8}{'ROI':>8}{'CI下限':>8}"
      f"{'100%超':>8}{'0円日':>7}{'中央払戻':>10}{'採る順位':>18}")
def rep(seg,B=2000):
    by=defaultdict(lambda:[0.0,0.0])
    for d,b,p,n,rk in seg:
        a=by[d]; a[0]+=b; a[1]+=p
    v=list(by.values()); bet=np.array([x[0] for x in v]); pay=np.array([x[1] for x in v])
    idx=np.random.randint(0,len(v),size=(B,len(v)))
    bs=np.sort(pay[idx].sum(1)/bet[idx].sum(1)); rois=pay/bet
    pl=sorted(p for _,_,p,_,_ in seg if p>0)
    cnt=defaultdict(int)
    for _,_,_,_,rks in seg:
        for x in rks: cnt[x]+=1
    tot=sum(cnt.values())
    mix=" ".join(f"{k}:{cnt[k]/tot:.0%}" for k in sorted(cnt))
    return (st.mean([s[3] for s in seg]), sum(1 for s in seg if s[2]>0)/len(seg),
            pay.sum()/bet.sum(), bs[int(B*.025)], float((rois>=1).mean()),
            float((rois==0).mean()), (st.median(pl) if pl else 0), mix)
for alpha in (99, 1.0, 0.8, 0.6, 0.4, 0.2):
    for wn,f in (("探索",lambda d:d<"2026-05-01"),("確認",lambda d:d>="2026-05-01")):
        seg=[]
        for x in R:
            if not f(x["date"]): continue
            take=[c for c in x["cands"]
                  if c["key"]==x["base"] or (alpha!=99 and x["base_p"]>0
                                             and c["prob"]>=alpha*x["base_p"])]
            if not take: continue
            s=unit_stake(len(take))
            pay=next((int(x["bd"][c["key"]]*100)*s//100 for c in take if c["key"] in x["wins"]),0)
            seg.append((x["date"],s*len(take),pay,len(take),[c["rank"] for c in take]))
        if len(seg)<500: continue
        pts,hit,roi,lo,ov,ze,med,mix=rep(seg)
        mk=" 🟢" if lo>PAY else ""
        print(f"{('5のみ' if alpha==99 else alpha) if wn=='探索' else '':>6}{wn:>5}{pts:>7.2f}"
              f"{hit:>8.2%}{roi:>8.1%}{lo:>8.1%}{ov:>8.1%}{ze:>7.1%}{med:>10,.0f}  {mix}{mk}")
print("\n--- 順位5の優位は月次で安定しているか（1点・順位別ROI）---")
by=defaultdict(lambda: defaultdict(lambda:[0,0]))
for x in R:
    m=x["date"][:7]
    for c in x["cands"]:
        a=by[m][c["rank"]]; a[0]+=1
        if c["key"] in x["wins"]: a[1]+=x["bd"][c["key"]]*100
print(f"{'月':10}"+"".join(f"{f'順位{r}':>9}" for r in range(3,8)))
for m in sorted(by):
    print(f"{m:10}"+"".join(f"{(by[m][r][1]/(100*by[m][r][0]) if by[m][r][0] else 0):>9.1%}"
                            for r in range(3,8)))
