#!/usr/bin/env python3
"""軸2の信頼度で買い目の形を切り替える「組み合わせ戦略」の日次KPI。

前提は `exp_trio_shape_switch.py`（群×形の面）。ここはそれを1本の戦略に束ねて
現行相当（総流し5点）と比べる。

🔴 **「中は買わない」は群×形の表を見たあとで決めた後付けの選択。**
   確認窓で再現（84.0→82.8%）してはいるが、厳密には新しい前向き窓での検定が要る。
🔴 標準化と閾値は探索窓でだけ決めている。
"""
import sys, json, os, statistics as st, random, numpy as np, psycopg2
from collections import defaultdict
sys.path.insert(0,'.')
from src.strategy_wt import unit_stake
random.seed(201); np.random.seed(201)
rows=[]
with open("data/exp/tf_shape_cache4.jsonl") as f:
    for line in f:
        r=json.loads(line)
        if r.get("win") and r.get("bad"): rows.append(r)
con=psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur=con.cursor()
keys=[r["race_key"] for r in rows]; board=defaultdict(dict)
for i in range(0,len(keys),2000):
    cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                "where bet_type='trio' and race_key=any(%s) and odds_value>0",(keys[i:i+2000],))
    for rk,c,o in cur.fetchall():
        s=frozenset(int(x) for x in str(c).replace("=","-").split("-"))
        if len(s)==3: board[rk][s]=float(o)
R=[]
for r in rows:
    bd=board.get(r["race_key"])
    if not bd: continue
    p3={int(k):v for k,v in r["p3"].items()}; bad={int(k):v for k,v in r["bad"].items()}
    if len(p3)<7 or len(bad)<7: continue
    o=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]
    a1,a2=o[0],o[1]
    wins={frozenset(int(x) for x in w.split("-")) for w in r["win"]}
    def mk(legs):
        legs=[k for k in legs if k in bd]
        if not legs: return None
        s=unit_stake(len(legs))
        return (s*len(legs), next((int(bd[k]*100)*s//100 for k in legs if k in wins),0), len(legs))
    R.append(dict(date=r["race_date"], axis_sum=p3[a1]+p3[a2], bad2=bad[a2],
      A=mk([frozenset((a1,a2,o[4]))]),
      D=mk([frozenset((a1,a2,o[4])),frozenset((a1,a2,o[2]))]),
      FULL=mk([frozenset((a1,a2,o[i])) for i in (2,3,4,5,6)])))
sel=[x for x in R if x["date"]<"2026-05-01"]
ma,sa=st.mean([x["axis_sum"] for x in sel]),st.pstdev([x["axis_sum"] for x in sel])
mb,sb=st.mean([x["bad2"] for x in sel]),st.pstdev([x["bad2"] for x in sel])
for x in R: x["conf"]=(x["axis_sum"]-ma)/(sa or 1)-(x["bad2"]-mb)/(sb or 1)
q=np.quantile([x["conf"] for x in sel],[1/3,2/3])
def pick(x,strategy):
    if strategy=="現行相当(総流し5点)": return x["FULL"]
    if strategy=="全レース A(1点)": return x["A"]
    if strategy=="全レース D(2点)": return x["D"]
    if strategy=="切替: 高信頼D / それ以外A":
        return x["D"] if x["conf"]>=q[1] else x["A"]
    if strategy=="切替: 高信頼D / 低信頼A / 中は買わない":
        return x["D"] if x["conf"]>=q[1] else (x["A"] if x["conf"]<q[0] else None)
    return None
print(f"{'戦略':34}{'窓':>5}{'件/日':>6}{'的中%':>8}{'ROI':>8}"
      f"{'100%超':>8}{'0円日':>7}{'5万+/日':>8}{'中央払戻':>10}")
for strat in ("現行相当(総流し5点)","全レース A(1点)","全レース D(2点)",
              "切替: 高信頼D / それ以外A","切替: 高信頼D / 低信頼A / 中は買わない"):
    for wn,f in (("探索",lambda d:d<"2026-05-01"),("確認",lambda d:d>="2026-05-01")):
        by=defaultdict(list)
        for x in R:
            if not f(x["date"]): continue
            p=pick(x,strat)
            if p: by[x["date"]].append(p)
        days=[]
        for d,v in by.items():
            v=v[:20]
            days.append((sum(z[0] for z in v),sum(z[1] for z in v),len(v),
                         sum(1 for z in v if z[1]>0),sum(1 for z in v if z[1]>=50000),
                         [z[1] for z in v if z[1]>0]))
        n=len(days); bet=sum(x[0] for x in days); pay=sum(x[1] for x in days)
        rois=[x[1]/x[0] for x in days]; pl=sorted(p for x in days for p in x[5])
        print(f"{strat if wn=='探索' else '':34}{wn:>5}{st.mean([x[2] for x in days]):>6.1f}"
              f"{sum(x[3] for x in days)/sum(x[2] for x in days):>8.2%}{pay/bet:>8.1%}"
              f"{sum(1 for r in rois if r>=1)/n:>8.1%}{sum(1 for r in rois if r==0)/n:>7.1%}"
              f"{sum(x[4] for x in days)/n:>8.2f}{st.median(pl):>10,.0f}")
