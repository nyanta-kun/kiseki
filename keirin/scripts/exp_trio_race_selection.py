#!/usr/bin/env python3
"""三連複: **レース選別**が ROI を分けるか（相手選別との切り分け）。

🔴 7C は `axis_sum >= 1.44` を要求しているが、**それが最適かは測られていない**。
   ゲートをかけずに全数で勾配を見る。

実測の結論（2026-08-23）: **レース選別は効かない。相手選別だけが効く。**
どの選別も確認窓で「全レース」を上回らなかった。
"""
import sys, json, math, random, statistics as st, os, psycopg2, numpy as np
from collections import defaultdict
sys.path.insert(0,'.')
from src.strategy_wt import unit_stake, rank_7t1_is_cross_line, rank_7t1_is_target_race_type
random.seed(131); np.random.seed(131)
PAY=0.7485
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

def build(partners):
    rec=[]
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
        tot=sum(p3.values()) or 1
        q=[v/tot for v in p3.values()]
        ent=-sum(x*math.log(x+1e-12) for x in q)/math.log(len(q))
        lg={int(k):v for k,v in r["line_group"].items()}
        lp={int(k):v for k,v in r["line_pos"].items()}
        mk={int(k):(v or "") for k,v in (r.get("mark") or {}).items()}
        honmei=next((c for c,m in mk.items() if str(m).strip() in ("◎","1")),None)
        taikou=next((c for c,m in mk.items() if str(m).strip() in ("○","◯","2")),None)
        overlap=len({a1,a2}&{x for x in (honmei,taikou) if x})
        rec.append(dict(date=r["race_date"],bet=bet,pay=pay,
                        axis_sum=p3[a1]+p3[a2], gap12=p3[a1]-p3[a2], ent=ent,
                        overlap=overlap, cross=bool(rank_7t1_is_cross_line(p3,lg,lp)),
                        final=bool(rank_7t1_is_target_race_type(r.get("race_type")))))
    return rec

def roi_ci(seg,B=1500):
    by=defaultdict(lambda:[0,0])
    for x in seg:
        a=by[x["date"]]; a[0]+=x["bet"]; a[1]+=x["pay"]
    v=list(by.values()); bet=np.array([x[0] for x in v],float); pay=np.array([x[1] for x in v],float)
    idx=np.random.randint(0,len(v),size=(B,len(v)))
    b=np.sort(pay[idx].sum(1)/bet[idx].sum(1))
    return pay.sum()/bet.sum(), b[int(B*.025)]

for partners,label in (([3,4,5,6,7],"総流し3-7"),([5],"順位5のみ")):
    rec=build(partners)
    sel=[x for x in rec if x["date"]<"2026-05-01"]; conf=[x for x in rec if x["date"]>="2026-05-01"]
    print(f"\n===== 相手={label} （{len(rec):,}R）=====")
    print(f"{'レース選別':26}{'探索:R':>8}{'ROI':>8}{'CI下限':>8}{'確認:R':>8}{'ROI':>8}{'CI下限':>8}")
    def show(name,f):
        a=[x for x in sel if f(x)]; b=[x for x in conf if f(x)]
        if len(a)<400 or len(b)<400: return
        ra,la=roi_ci(a); rb,lb=roi_ci(b)
        m=" 🟢両窓" if la>PAY and lb>PAY else (" ⚠️探索のみ" if la>PAY else "")
        print(f"{name:26}{len(a):>8,}{ra:>8.1%}{la:>8.1%}{len(b):>8,}{rb:>8.1%}{lb:>8.1%}{m}")
    show("全レース", lambda x: True)
    qs=np.quantile([x["axis_sum"] for x in sel],[.2,.4,.6,.8])
    for i in range(5):
        lo=-9 if i==0 else qs[i-1]; hi=9 if i==4 else qs[i]
        show(f"axis_sum Q{i+1} ({lo:.2f}〜{hi:.2f})", lambda x,l=lo,h=hi: l<=x["axis_sum"]<h)
    show("axis_sum >= 1.44 (7C現行)", lambda x: x["axis_sum"]>=1.44)
    show("axis_sum < 1.44", lambda x: x["axis_sum"]<1.44)
    qe=np.quantile([x["ent"] for x in sel],[.33,.66])
    show("entropy 低(堅い)", lambda x: x["ent"]<qe[0])
    show("entropy 高(混戦)", lambda x: x["ent"]>=qe[1])
    for o in (0,1,2):
        show(f"印との一致 overlap={o}", lambda x,o=o: x["overlap"]==o)
    show("別ライン", lambda x: x["cross"]); show("同ライン", lambda x: not x["cross"])
    show("決勝系", lambda x: x["final"]); show("決勝系以外", lambda x: not x["final"])
