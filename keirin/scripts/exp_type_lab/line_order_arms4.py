#!/usr/bin/env python3
"""第7章 ユーザー指定の3量を「並べ替えの規則」として直接測る（2026-09-10）。

① ライン内の順番（隊列順 ↔ 番手が差す）→ λr の較正値 1.9 が土台
② ライン毎の総得点            → そのラインが総得点最大か / ライン規模
③ ライン先頭・番手の複勝率      → `wt_entries.third_rate`（3連対率）・`first_rate`

いずれも「λr を条件で二値に振る」形。**帯は守る**（DESIGN 2.4 の価格の道具を壊さない）。
"""
from __future__ import annotations
import itertools, pickle, sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from line_order_arms import build_ctx, trio_probs, variant_probs  # noqa: E402
from line_order_arms3 import reorder  # noqa: E402
from line_order_build import AXIS_GATE_MIN, MIN_MEAN_PAYOUT, MIN_POINT_ODDS, _plan_for, load_rates  # noqa: E402
from src.type_lab import PLANS, allocate, apply_line_swap, build_legs, mean_expected_payout  # noqa: E402


def _c_rate3(L,S,x):
    a,b=x.rates.get(S,(-1,-1,-1))[2], x.rates.get(L,(-1,-1,-1))[2]
    return a>b if min(a,b)>=0 else False
def _c_rate1(L,S,x):
    a,b=x.rates.get(S,(-1,-1,-1))[0], x.rates.get(L,(-1,-1,-1))[0]
    return a>b if min(a,b)>=0 else False
def _c_rp(L,S,x): return x.rp.get(S,0)>x.rp.get(L,0)
def _c_topline(L,S,x):
    ls=x.shape.lines
    if not ls: return False
    j=[k for k,ln in enumerate(ls) if L in ln]
    if not j: return False
    best=max(range(len(ls)), key=lambda k: sum(x.rp.get(c,0.0) for c in ls[k]))
    return j[0]==best
def _c_size3(L,S,x):
    for ln in x.shape.lines:
        if L in ln: return len(ln)>=3
    return False

ARMS4 = [
    ("一律 λr1.9(土台)",      None,      (1.9, 1.9)),
    ("③3連対率 2.6/1.3",     _c_rate3,  (2.6, 1.3)),
    ("③勝率 2.6/1.3",        _c_rate1,  (2.6, 1.3)),
    ("競走得点 2.6/1.3",      _c_rp,     (2.6, 1.3)),
    ("②総得点最大ライン 1.5/2.4", _c_topline,(1.5, 2.4)),
    ("②ライン3車以上 2.4/1.5",  _c_size3,  (2.4, 1.5)),
]


def main():
    z=C.board()
    a={k:z[k] for k in ("P3","PW","LG","A_line_pos","ST","A_race_point","BEHIND",
                        "DAYI","PO","TRIO_PO","TRIO_ODDS","TRIO_WIN","TRIO_PAY",
                        "WIN","PAY","DATE","TYPE","RTYPE","KEY","AXIS_SUM")}
    rates=load_rates(); tp=np.array([str(v) for v in a["TYPE"]])
    rt=np.array([str(v) for v in a["RTYPE"]])
    res={w:{nm:[] for nm,*_ in ARMS4} for w in ("explore","confirm")}
    for w in res: res[w]["現行"]=[]
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
                return dict(i=i,date=x.date,plan=key,trio=trio,inv=inv,pay=pay,
                            shown=pay>=inv,in_legs=inl,set_hit=seth)
            res[win]["現行"].append(_rec(legs,st))
            for nm,cond,ws in ARMS4:
                if trio:
                    res[win][nm].append(_rec(legs,st)); continue
                if cond is None:
                    prv=variant_probs(x.p0,x.lg,x.lp,2.0,1.5,ws[0],1.0)
                else:
                    f=(lambda L,S,_x=x,_c=cond,_w=ws: _w[0] if _c(L,S,_x) else _w[1])
                    prv=variant_probs(x.p0,x.lg,x.lp,2.0,1.5,1.0,1.0,cond=True,lamr2=f)
                nl=reorder(legs, lambda p,_v=prv: _v.get(p,0.0), pod, plan, True)
                if set(nl)==set(legs):
                    res[win][nm].append(_rec(legs,st)); continue
                s2=allocate(nl,pod,prb,plan)
                ok=(s2 and len(s2)==len(nl)
                    and mean_expected_payout(s2,pod)>MIN_MEAN_PAYOUT
                    and min(float(pod[c]) for c in s2)>=MIN_POINT_ODDS)
                res[win][nm].append(_rec(nl,s2) if ok else _rec(legs,st))
    pickle.dump(res,open("/tmp/lo/arms4.pkl","wb")); print("→ /tmp/lo/arms4.pkl")

if __name__=="__main__":
    main()
