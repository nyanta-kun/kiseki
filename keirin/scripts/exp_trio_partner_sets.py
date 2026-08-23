#!/usr/bin/env python3
"""三連複: 相手セットを変えたときの**商品単位**の効き幅（探索/確認窓 + 日次KPI）。

🔴 部分集団の分離幅と商品への効き幅は別物。同一レースの対応比較で測る。
🔴 予算は1レース1万円で固定なので、**点数を減らすと1点あたりの賭け金が増え払戻も増える**。
   点数を変える比較では的中率と払戻額が必ず逆方向に動くので、
   ROI・100%超の日・0円の日を同時に見ること。
"""
import sys, json, random, statistics as st, os, psycopg2
from collections import defaultdict
sys.path.insert(0,'.')
from src.strategy_wt import unit_stake
random.seed(121)
rows=[]
with open("data/exp/tf_shape_cache.jsonl") as f:
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
SETS={"総流し 3-7":[3,4,5,6,7],"3,5,6":[3,5,6],"3,5":[3,5],"5,6":[5,6],"順位5のみ":[5]}
def per_race(partners):
    out=[]
    for r in rows:
        bd=board.get(r["race_key"])
        if not bd: continue
        p3={int(k):v for k,v in r["p3"].items()}
        order=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]
        if len(order)<7: continue
        a1,a2=order[0],order[1]
        legs=[frozenset((a1,a2,order[i-1])) for i in partners
              if frozenset((a1,a2,order[i-1])) in bd]
        if not legs: continue
        stake=unit_stake(len(legs)); bet=stake*len(legs)
        wins={frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        pay=next((int(bd[k]*100)*stake//100 for k in legs if k in wins),0)
        out.append((r["race_date"],bet,pay))
    return out
print(f"{'相手セット':14}{'窓':>6}{'R数':>8}{'的中%':>7}{'ROI':>8}"
      f"{'件/日20件':>10}{'100%超':>8}{'0円日':>7}{'20万+/日':>9}{'中央払戻':>10}")
for name,ps in SETS.items():
    d=per_race(ps)
    for wname,f in (("探索",lambda x:x[0]<"2026-05-01"),("確認",lambda x:x[0]>="2026-05-01")):
        seg=[x for x in d if f(x)]
        bet=sum(x[1] for x in seg); pay=sum(x[2] for x in seg)
        by=defaultdict(list)
        for dt,b,p in seg: by[dt].append((b,p))
        days=[]
        for dt,v in by.items():
            v=v[:20]
            days.append((sum(x[0] for x in v),sum(x[1] for x in v),len(v),
                         sum(1 for x in v if x[1]>=200000)))
        rois=[x[1]/x[0] for x in days]; n=len(days)
        pl=sorted(x[2] for x in seg if x[2]>0)
        print(f"{name if wname=='探索' else '':14}{wname:>6}{len(seg):>8,}"
              f"{sum(1 for x in seg if x[2]>0)/len(seg):>7.2%}{pay/bet:>8.1%}"
              f"{st.mean([x[2] for x in days]):>10.1f}"
              f"{sum(1 for r in rois if r>=1)/n:>8.1%}{sum(1 for r in rois if r==0)/n:>7.1%}"
              f"{sum(x[3] for x in days)/n:>9.2f}{st.median(pl):>10,.0f}")
