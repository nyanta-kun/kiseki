"""看板枠の一般形ビルダー（本番 signboard の拡張）と採点。

本番 `type_lab.build_legs(structure='signboard')` と同一の詰め方を再現したうえで、
max_legs / min_odds / max_odds / 配分 / 券種 を動かせるようにしたもの。
"""
from __future__ import annotations
import sys, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
from src import type_lab as TL
BUDGET=10_000; UNIT=100

def signboard_legs(r, T, bet_type="trifecta", max_legs=0, min_odds=0.0,
                   max_odds=600.0, bust=False, order="prob", rng=None):
    odds = r["tpo"] if bet_type=="trio" else r["po"]
    prob = r["tprob"] if bet_type=="trio" else r["probs"]
    pool = set(r["shape"].order[1:]) if bust else None
    cand=[k for k,v in odds.items()
          if v and v>0 and len(set(k))==3
          and (pool is None or set(k)<=pool)
          and float(v)>=min_odds and (not max_odds or float(v)<=max_odds)]
    if order=="prob":
        cand.sort(key=lambda k: -float(prob.get(k,0.0)))
    elif order=="rand":
        rng.shuffle(cand)
    elif order=="odds":                       # 予測オッズ昇順（人気順）
        cand.sort(key=lambda k: float(odds[k]))
    cap=float(BUDGET)/float(T)
    out=[]; s=0.0
    for k in cand:
        o=float(odds[k])
        if s+1.0/o > cap: continue
        out.append(k); s+=1.0/o
        if max_legs and len(out)>=max_legs: break
    return (out, odds, prob) if out else (None,odds,prob)

def alloc(legs, odds, prob, mode="dutch"):
    if mode=="dutch":
        w=[1.0/float(odds[c]) for c in legs]
    elif mode=="equal":
        w=[1.0]*len(legs)
    elif mode=="prob":
        w=[max(float(prob.get(c,0.0)),1e-12) for c in legs]
    n=BUDGET//UNIT
    if len(legs)>n: return None
    units=TL._proportional(w,n)
    st={c:u*UNIT for c,u in zip(legs,units)}
    legs2=[c for c in legs if st[c]>0]
    if len(legs2)!=len(legs):
        if not legs2: return None
        return alloc(legs2, odds, prob, mode)
    return st

def score(r, st, bet_type, odds):
    bet=sum(st.values())
    if bet_type=="trio":
        win=r["twin"]; payout=st[win]*r["tpay"] if win in st else 0.0
    else:
        win=r["win"]; payout=st[win]/100.0*r["pay"] if win in st else 0.0
    return dict(key=r["key"], date=r["date"], bet=bet, payout=payout,
                hit=win in st, n=len(st),
                mean_plan=sum(st[c]*float(odds[c]) for c in st)/len(st),
                plan_pay_of_win=(st[win]*float(odds[win])) if win in st else None)

def build(races, T, pop=None, gate=True, amode="dutch", **kw):
    out=[]
    for r in races:
        if pop and not pop(r): continue
        legs,odds,prob=signboard_legs(r,T,**kw)
        if not legs: continue
        st=alloc(legs,odds,prob,amode)
        if not st: continue
        if gate:
            mp=sum(st[c]*float(odds[c]) for c in st)/len(st)
            if mp<=20000: continue
            if min(float(odds[c]) for c in st)<2.0: continue
        out.append(score(r,st,kw.get("bet_type","trifecta"),odds))
    return out
