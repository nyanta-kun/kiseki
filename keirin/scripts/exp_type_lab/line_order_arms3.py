#!/usr/bin/env python3
"""第6章 並べ替えの規則を選ぶ（2026-09-10）。

🔴 `line_order_arms2` の `reorder` は **プランの帯（`min_odds`）を見ていない**。
   `E_hit`(30倍+) / `C_hit`(15倍+) / `F_hit`(5倍+) で帯の下の並びを買ってしまい、
   それは DESIGN 2.4 の「価格の道具」を黙って外す操作になる。
   ここでは **帯を守る版と守らない版を分けて**測る。
"""
from __future__ import annotations
import itertools, pickle, sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from line_order_arms import build_ctx, trio_probs, variant_probs  # noqa: E402
from line_order_build import AXIS_GATE_MIN, MIN_MEAN_PAYOUT, MIN_POINT_ODDS, _plan_for, load_rates  # noqa: E402
from src.type_lab import PLANS, allocate, apply_line_swap, build_legs, mean_expected_payout  # noqa: E402

PERMS, C3 = C.CANON, C.CANON3


def reorder(legs, score, pod, plan, band: bool):
    have = set(legs); out = []
    lo = float(plan.min_odds or 0.0); hi = float(plan.max_odds or 0.0)
    for c in legs:
        alts = []
        for p in itertools.permutations(sorted(set(c))):
            if p != c and p in have:
                continue
            o = pod.get(p)
            if not o:
                continue
            if band and p != c and (float(o) < lo or (hi and float(o) > hi)):
                continue
            alts.append(p)
        best = max(alts, key=lambda p: score(p)) if alts else c
        if best != c:
            have.discard(c); have.add(best)
        out.append(best)
    return out


ARMS3 = [
    ("λr1.9 帯順守",   "prob", 1.9, True),
    ("λr1.9 帯無視",   "prob", 1.9, False),
    ("市場順 帯順守",   "mkt",  0.0, True),
    ("市場順 帯無視",   "mkt",  0.0, False),
    ("確率÷√市場 帯順守", "mix", 1.9, True),
]


def main():
    z = C.board()
    a = {k: z[k] for k in ("P3","PW","LG","A_line_pos","ST","A_race_point","BEHIND",
                           "DAYI","PO","TRIO_PO","TRIO_ODDS","TRIO_WIN","TRIO_PAY",
                           "WIN","PAY","DATE","TYPE","RTYPE","KEY","AXIS_SUM")}
    rates = load_rates(); tp = np.array([str(v) for v in a["TYPE"]])
    rt = np.array([str(v) for v in a["RTYPE"]])
    res = {w: {nm: [] for nm, *_ in ARMS3} for w in ("explore","confirm")}
    for w in res: res[w]["現行"] = []
    for win in ("explore","confirm"):
        idx=[int(i) for i in C.select(None,win) if tp[int(i)] in "ABCDEF"]
        for n,i in enumerate(idx):
            if n%5000==0: print(f"  {win} {n:,}/{len(idx):,}",flush=True)
            x=build_ctx(a,i,rates)
            if x is None or x.shape.type_label!=tp[i]: continue
            pr=variant_probs(x.p0,x.lg,x.lp,2.0,1.5,1.0,1.0); pr3=trio_probs(pr)
            def _mk(key):
                plan=PLANS[key]; trio=plan.bet_type=="trio"
                pod,prb=((x.po_t3,pr3) if trio else (x.po_tf,pr))
                l=build_legs(x.shape,plan,pod,prb)
                if not l: return None
                st=allocate(l,pod,prb,plan)
                if not st: return None
                if not trio: l,st=apply_line_swap(x.shape,plan,l,st,pod,prb)
                m=mean_expected_payout(st,pod)
                if m<=MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st)<MIN_POINT_ODDS: return None
                return plan,trio,pod,prb,list(st),st
            trio_ok=(_mk("A_trio") is not None) if tp[i]=="A" else False
            key=_plan_for(tp[i],rt[i],x.shape.pw_ent,trio_ok)
            if not key or float(a["AXIS_SUM"][i])<AXIS_GATE_MIN.get(key,0.0): continue
            got=_mk(key)
            if got is None: continue
            plan,trio,pod,prb,legs,st=got
            def _rec(l2,s2):
                inv=float(sum(s2.values()))
                if trio:
                    pay=float(s2[x.win_t3]*x.odds_t3) if x.win_t3 in s2 else 0.0
                    inl=x.win_t3 in s2; seth=inl
                else:
                    pay=float(s2[x.win_tf]/100.0*x.pay_tf*100.0) if x.win_tf in s2 else 0.0
                    inl=x.win_tf in s2
                    seth=frozenset(x.win_tf) in {frozenset(c) for c in s2}
                return dict(i=i,date=x.date,plan=key,trio=trio,k=len(s2),inv=inv,pay=pay,
                            shown=pay>=inv,hit=pay>0,in_legs=inl,set_hit=seth)
            res[win]["現行"].append(_rec(legs,st))
            for nm,kind,lr,band in ARMS3:
                if trio:
                    res[win][nm].append(_rec(legs,st)); continue
                if kind=="prob":
                    prv=variant_probs(x.p0,x.lg,x.lp,2.0,1.5,lr,1.0)
                    sc=lambda p,_v=prv: _v.get(p,0.0)
                elif kind=="mkt":
                    sc=lambda p: -float(pod.get(p,9e9))
                else:
                    prv=variant_probs(x.p0,x.lg,x.lp,2.0,1.5,lr,1.0)
                    sc=lambda p,_v=prv: _v.get(p,0.0)/max(float(pod.get(p,1e9)),1e-9)**0.5
                nl=reorder(legs,sc,pod,plan,band)
                if set(nl)==set(legs):
                    res[win][nm].append(_rec(legs,st)); continue
                s2=allocate(nl,pod,prb,plan)
                ok=(s2 and len(s2)==len(nl)
                    and mean_expected_payout(s2,pod)>MIN_MEAN_PAYOUT
                    and min(float(pod[c]) for c in s2)>=MIN_POINT_ODDS)
                res[win][nm].append(_rec(nl,s2) if ok else _rec(legs,st))
    pickle.dump(res,open("/tmp/lo/arms3.pkl","wb")); print("→ /tmp/lo/arms3.pkl")


if __name__=="__main__":
    main()
