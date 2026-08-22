#!/usr/bin/env python3
"""ペアモデルの較正 — 外れは「低確率の裾」か、予測できる傾向か。

🔴 較正が合っていれば外れは情報の限界（＝傾向は無い）。特定の帯やセグメントで
   実測が予測からずれるなら、そこが残っている余地。

実測（2026・14,941R）: 較正誤差は帯ごとに ±2.5pt 以内、セグメント残差は ±2pt 以内。
**予測できる傾向は残っていない。** 唯一の兆候は高確率帯（84%予測→実測81.5%）の
2.5pt の過信。
"""
import sys, json, os, itertools, numpy as np, psycopg2
from collections import defaultdict
sys.path.insert(0,'.')
sys.argv=["x"]
import importlib
mod=importlib.import_module("scripts.exp_trio_pair_model")
from scripts.backfill_7t1_rank_wt import _load_finishes
import lightgbm as lgb
np.random.seed(351)
tr=[dict(key=r["race_key"],date=r["race_date"],order=r["order"],
         p3={int(k):v for k,v in r["p3"].items()})
    for r in map(json.loads, open("data/exp/trio_rank_cache.jsonl"))]
te=[]; extra={}
with open("data/exp/tf_shape_cache4.jsonl") as f:
    for x in f:
        r=json.loads(x)
        if not r.get("win"): continue
        p3={int(k):v for k,v in r["p3"].items()}
        te.append(dict(key=r["race_key"],date=r["race_date"],p3=p3,
                       order=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]))
        extra[r["race_key"]]=dict(race_type=r.get("race_type"),
                                  bad={int(k):v for k,v in (r.get("bad") or {}).items()},
                                  lg={int(k):v for k,v in r["line_group"].items()})
etr=mod.load_entries([r["key"] for r in tr]); ete=mod.load_entries([r["key"] for r in te])
ftr=_load_finishes([r["key"] for r in tr]); fte=_load_finishes([r["key"] for r in te])
Xtr,ytr,_=mod.build_rows(tr,etr,ftr); Xte,yte,mte=mod.build_rows(te,ete,fte)
m=lgb.train(dict(objective="binary",learning_rate=0.05,num_leaves=31,
                 min_data_in_leaf=200,feature_fraction=0.8,bagging_fraction=0.8,
                 bagging_freq=1,verbose=-1,seed=7),
            lgb.Dataset(Xtr,label=ytr),num_boost_round=400)
pred=m.predict(Xte)
best={}
for (key,date,a,b,ra,rb),p,t in zip(mte,pred,yte):
    if key not in best or p>best[key][0]: best[key]=(p,a,b,int(t),date)
print(f"検定 {len(best):,}R\n=== 較正: ペアモデルの予測 vs 実測（採用ペアのみ）===")
print(f"{'予測帯':>14}{'R数':>8}{'予測平均':>9}{'実測':>8}{'差':>8}")
v=sorted(best.values())
q=np.quantile([x[0] for x in v],[.1,.25,.5,.75,.9])
edges=[0]+list(q)+[1]
for i in range(len(edges)-1):
    seg=[x for x in v if edges[i]<=x[0]<edges[i+1]]
    if len(seg)<200: continue
    pm=np.mean([x[0] for x in seg]); ac=np.mean([x[3] for x in seg])
    print(f"{f'{edges[i]:.2f}〜{edges[i+1]:.2f}':>14}{len(seg):>8,}{pm:>9.1%}{ac:>8.1%}{ac-pm:>+8.1pt}"
          if False else
          f"{f'{edges[i]:.2f}〜{edges[i+1]:.2f}':>14}{len(seg):>8,}{pm:>9.1%}{ac:>8.1%}{(ac-pm)*100:>+7.1f}pt")
print("\n=== セグメント別の残差（実測 − 予測）===")
print(f"{'セグメント':24}{'R数':>8}{'予測':>8}{'実測':>8}{'残差':>9}")
def seg(name,f):
    s=[x for x in v if f(x)]
    if len(s)<300: return
    pm=np.mean([x[0] for x in s]); ac=np.mean([x[3] for x in s])
    print(f"{name:24}{len(s):>8,}{pm:>8.1%}{ac:>8.1%}{(ac-pm)*100:>+8.1f}pt")
ex=lambda x: extra.get([k for k,vv in best.items() if vv is x][0]) if False else None
key_of={id(vv):k for k,vv in best.items()}
def E(x): return extra.get(key_of[id(x)],{})
seg("全体", lambda x: True)
for rt in ("決勝","準決勝","特選","予選","一般","選抜"):
    seg(f"レース種別={rt}", lambda x,rt=rt: str(E(x).get("race_type") or "")==rt)
seg("軸が同ライン", lambda x: (lambda e: e.get("lg",{}).get(x[1]) is not None
        and e.get("lg",{}).get(x[1])==e.get("lg",{}).get(x[2]))(E(x)))
seg("軸が別ライン", lambda x: (lambda e: e.get("lg",{}).get(x[1])!=e.get("lg",{}).get(x[2]))(E(x)))
b=[np.mean([E(x).get("bad",{}).get(x[1],0),E(x).get("bad",{}).get(x[2],0)]) for x in v]
th=np.quantile([z for z in b if z>0],[.25,.75]) if any(b) else [0,0]
seg("軸の大敗率が低い(下位25%)", lambda x: np.mean([E(x).get("bad",{}).get(x[1],0),
                                             E(x).get("bad",{}).get(x[2],0)])<=th[0])
seg("軸の大敗率が高い(上位25%)", lambda x: np.mean([E(x).get("bad",{}).get(x[1],0),
                                             E(x).get("bad",{}).get(x[2],0)])>=th[1])
