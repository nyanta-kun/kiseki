#!/usr/bin/env python3
"""レース形状シグナルが市場に織り込まれていることを直接示す。

的中率が上がる帯では確定オッズがちょうど同じだけ下がり、積（ROI）が残らない。
🔴 このリポジトリの一般則の実例:
   **「市場と同じ向きの分類器は、精度がどれだけ高くても ROI にならない」**（7H2 の否定結果）
"""
import sys, json, os, psycopg2, numpy as np
from collections import defaultdict
sys.path.insert(0,'.')
from src.strategy_wt import unit_stake
np.random.seed(151)
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
rec=[]
for r in rows:
    bd=board.get(r["race_key"])
    if not bd: continue
    p3={int(k):v for k,v in r["p3"].items()}
    order=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]
    if len(order)<7: continue
    v=[p3[c] for c in order]
    key=frozenset((order[0],order[1],order[4]))
    if key not in bd: continue
    wins={frozenset(int(x) for x in w.split("-")) for w in r["win"]}
    rec.append(dict(date=r["race_date"], spread36=v[2]-v[5],
                    odds=bd[key], hit=1 if key in wins else 0))
sel=[x for x in rec if x["date"]<"2026-05-01"]; conf=[x for x in rec if x["date"]>="2026-05-01"]
qs=np.quantile([x["spread36"] for x in sel],[.25,.5,.75])
print("相手=順位5 の三連複 {a1,a2,5} を、相手候補(3〜6位)の団子度で4分位に切る")
print("🔴 的中率が上がる帯ほど**確定オッズが下がる**＝市場が既に織り込んでいる\n")
print(f"{'spread36':>12}{'窓':>5}{'R数':>7}{'的中率':>8}{'確定ｵｯｽﾞ中央':>13}"
      f"{'的中率×中央ｵｯｽﾞ':>15}{'実現ROI':>9}")
for i in range(4):
    lo=-9 if i==0 else qs[i-1]; hi=9 if i==3 else qs[i]
    for wn,seg in (("探索",sel),("確認",conf)):
        s=[x for x in seg if lo<=x["spread36"]<hi]
        if len(s)<300: continue
        h=np.mean([x["hit"] for x in s]); mo=np.median([x["odds"] for x in s])
        roi=sum(x["odds"] for x in s if x["hit"])/len(s)
        print(f"{f'Q{i+1}(団子←→バラけ)' if wn=='探索' else '':>12}{wn:>5}{len(s):>7,}"
              f"{h:>8.2%}{mo:>13.1f}{h*mo:>15.2f}{roi:>9.1%}")
