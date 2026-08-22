#!/usr/bin/env python3
"""どういう条件で軸2が飛ぶか（＝二軸の形が成立しないレースの特定）。

ユーザー指摘（2026-08-23）「軸2が外れるケースが多く、条件による軸2の精度は
現在以上に上げられる」への検証。

🔴 **ROI で市場に勝つ話ではない。** 実測で二軸的中率は 36%〜75.5% と2倍以上の幅で
   動き、探索窓と確認窓でほぼ完全に一致する（＝予測は本物）。だが ROI は
   同じ条件で切っても動かない（市場が織り込んでいる・`exp_trio_shape_priced.py`）。
   使い道は「軸2が飛ぶレースでは二軸の形自体を変える」という**設計判断**。
"""
import sys, json, statistics as st, numpy as np
sys.path.insert(0,'.')
np.random.seed(181)
rows=[]
with open("data/exp/tf_shape_cache4.jsonl") as f:
    for line in f:
        r=json.loads(line)
        if r.get("win") and r.get("bad"): rows.append(r)
rec=[]
for r in rows:
    p3={int(k):v for k,v in r["p3"].items()}
    bad={int(k):v for k,v in r["bad"].items()}
    if len(p3)<7 or len(bad)<7: continue
    order=[c for c,_ in sorted(p3.items(),key=lambda kv:(-kv[1],kv[0]))]
    a1,a2=order[0],order[1]; v=[p3[c] for c in order]
    lg={int(k):x for k,x in r["line_group"].items()}
    mk={int(k):(x or "") for k,x in (r.get("mark") or {}).items()}
    honmei=next((c for c,m in mk.items() if str(m).strip() in ("◎","1")),None)
    taikou=next((c for c,m in mk.items() if str(m).strip() in ("○","◯","2")),None)
    top3={int(x) for w in r["win"] for x in w.split("-")}
    rec.append(dict(date=r["race_date"], a1_in=int(a1 in top3), a2_in=int(a2 in top3),
                    both=int(a1 in top3 and a2 in top3),
                    gap12=v[0]-v[1], gap23=v[1]-v[2], axis_sum=v[0]+v[1],
                    bad2=bad[a2], same_line=int(lg.get(a1)==lg.get(a2)),
                    a2_is_mark=int(a2 in (honmei,taikou)),
                    a1_is_honmei=int(a1==honmei)))
sel=[x for x in rec if x["date"]<"2026-05-01"]; conf=[x for x in rec if x["date"]>="2026-05-01"]
print(f"{len(rec):,}R  全体: 軸1の3着内 {np.mean([x['a1_in'] for x in rec]):.2%} / "
      f"軸2の3着内 {np.mean([x['a2_in'] for x in rec]):.2%} / 二軸 {np.mean([x['both'] for x in rec]):.2%}\n")
print(f"{'条件':30}{'探索:R':>8}{'軸2の3着内':>11}{'二軸':>8}{'確認:R':>8}{'軸2の3着内':>11}{'二軸':>8}")
def show(name,f):
    a=[x for x in sel if f(x)]; b=[x for x in conf if f(x)]
    if len(a)<400 or len(b)<400: return
    print(f"{name:30}{len(a):>8,}{np.mean([x['a2_in'] for x in a]):>11.2%}"
          f"{np.mean([x['both'] for x in a]):>8.2%}{len(b):>8,}"
          f"{np.mean([x['a2_in'] for x in b]):>11.2%}{np.mean([x['both'] for x in b]):>8.2%}")
show("全レース", lambda x: True)
for feat in ("gap12","gap23","axis_sum","bad2"):
    qs=np.quantile([x[feat] for x in sel],[.25,.5,.75])
    for i in range(4):
        lo=-9 if i==0 else qs[i-1]; hi=9 if i==3 else qs[i]
        show(f"  {feat} Q{i+1}", lambda x,l=lo,h=hi,f=feat: l<=x[f]<h)
show("軸1と軸2が同ライン", lambda x: x["same_line"]==1)
show("軸1と軸2が別ライン", lambda x: x["same_line"]==0)
show("軸2が印(◎○)を持つ", lambda x: x["a2_is_mark"]==1)
show("軸2が無印", lambda x: x["a2_is_mark"]==0)
show("軸1が◎", lambda x: x["a1_is_honmei"]==1)
show("軸1が◎でない", lambda x: x["a1_is_honmei"]==0)
