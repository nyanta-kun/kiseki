#!/usr/bin/env python3
"""「総流し5点 × 堅い上位10%」の日次KPI（**1日の件数上限なし**）。

ユーザー指示（2026-08-23）「日に20件の制約を持たず、確実に買えるレースを厳選すると
どうなるか」への回答。上限を置くと厳選の効果と上限による選択が混ざるので外す。

🔴 **厳選が効くのは買い目を絞っていない形に対してだけ。**
   1点（順位5）に絞った形では厳選が逆効果（83.6%→70.5%）。両方やると悪化する。
   天井は 82〜84% で、そこへは「買い目を絞る」か「レースを絞る」かの
   **どちらか一方**で届く。
"""
import sys, json, os, statistics as st, numpy as np, psycopg2
from collections import defaultdict
sys.path.insert(0,'.')
from src.strategy_wt import unit_stake
np.random.seed(251)
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
    p3={int(k):v for k,v in r["p3"].items()}
    if len(p3)<7: continue
    o=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]
    a1,a2=o[0],o[1]
    wins={frozenset(int(x) for x in w.split("-")) for w in r["win"]}
    def mk(idx):
        legs=[frozenset((a1,a2,o[i])) for i in idx]; legs=[k for k in legs if k in bd]
        if not legs: return None
        s=unit_stake(len(legs))
        return dict(bet=s*len(legs), pay=next((int(bd[k]*100)*s//100 for k in legs if k in wins),0),
                    odds=st.mean([bd[k] for k in legs]))
    R.append(dict(date=r["race_date"], full=mk([2,3,4,5,6]), one=mk([4])))
R=[x for x in R if x["full"] and x["one"]]
sel=[x for x in R if x["date"]<"2026-05-01"]
thr=np.quantile([x["full"]["odds"] for x in sel], 0.10)
print(f"堅さの閾値（総流し5点の平均予測オッズ）= {thr:.2f}倍以下\n")
def kpi(name, pick):
    for wn,g in (("探索",lambda d:d<"2026-05-01"),("確認",lambda d:d>="2026-05-01")):
        by=defaultdict(list)
        for x in R:
            if not g(x["date"]): continue
            p=pick(x)
            if p: by[x["date"]].append(p)
        days=[]
        for d,v in by.items():
            days.append((sum(z["bet"] for z in v),sum(z["pay"] for z in v),len(v),
                         sum(1 for z in v if z["pay"]>0),[z["pay"] for z in v if z["pay"]>0]))
        n=len(days); bet=sum(x[0] for x in days); pay=sum(x[1] for x in days)
        rois=[x[1]/x[0] for x in days]; pl=sorted(p for x in days for p in x[4])
        print(f"{name if wn=='探索' else '':30}{wn:>5}{st.mean([x[2] for x in days]):>6.1f}"
              f"{st.mean([x[0] for x in days]):>10,.0f}"
              f"{sum(x[3] for x in days)/sum(x[2] for x in days):>8.2%}{pay/bet:>8.1%}"
              f"{sum(1 for r in rois if r>=1)/n:>8.1%}{sum(1 for r in rois if r==0)/n:>7.1%}"
              f"{(st.median(pl) if pl else 0):>10,.0f}")
print(f"{'構成（上限なし）':30}{'窓':>5}{'件/日':>6}{'投資/日':>10}{'的中%':>8}"
      f"{'ROI':>8}{'100%超':>8}{'0円日':>7}{'中央払戻':>10}")
kpi("現行相当: 総流し5点 全レース", lambda x: x["full"])
kpi("1点(順位5) 全レース", lambda x: x["one"])
kpi("総流し5点 × 堅い上位10%", lambda x: x["full"] if x["full"]["odds"]<=thr else None)
kpi("1点(順位5) × 堅い上位10%", lambda x: x["one"] if x["full"]["odds"]<=thr else None)
