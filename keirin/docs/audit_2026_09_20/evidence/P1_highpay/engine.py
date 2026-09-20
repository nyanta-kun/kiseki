"""買い目を組む → 確定オッズで採点 → 指標にまとめる。"""
from __future__ import annotations
import sys, math, random
import numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib
from src import type_lab as TL
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS

BANDS = [(0,15),(15,30),(30,60),(60,100),(100,300),(300,600),(600,1e9)]
def band_of(o):
    for lo,hi in BANDS:
        if lo<=o<hi: return f"{lo}-{hi if hi<1e8 else '∞'}"
    return "?"

def gate_pass(legs, stakes, odds):
    if TL.mean_expected_payout(stakes, odds) <= MIN_MEAN_PAYOUT: return False
    o=[float(odds[c]) for c in legs]
    return not (o and min(o) < MIN_POINT_ODDS)

def evaluate(r, legs, stakes, bet_type, odds):
    bet=sum(stakes.values())
    if bet_type=="trio":
        win, fin = r["twin"], r["tpay"]         # tpay = 確定三連複オッズ(倍)
        payout = stakes[win]*fin if (win is not None and win in stakes) else 0.0
    else:
        win, pay = r["win"], r["pay"]           # pay = 円/100円
        fin = pay/100.0
        payout = stakes[win]/100.0*pay if (win is not None and win in stakes) else 0.0
    hit = payout>0 or (win is not None and win in stakes)
    plan_pay = (stakes[win]*float(odds[win])) if (win is not None and win in stakes) else None
    return dict(bet=bet, payout=payout, hit=bool(hit), n=len(legs),
                mean_plan=TL.mean_expected_payout(stakes, odds),
                plan_pay_of_win=plan_pay, fin_win=fin if hit else None,
                bands=[(band_of(float(odds[c])), stakes[c]) for c in legs])

def run_plan(races, plan, pop_filter=None, need_gate=True):
    """races 上で plan を組んで採点。戻り: list[dict]"""
    out=[]
    for r in races:
        if r["type"]!=plan.type_label and plan.type_label!="*": continue
        if pop_filter and not pop_filter(r): continue
        got = TL.build_with_gate_fallback(r["shape"], plan,
                r["tpo"] if plan.bet_type=="trio" else r["po"],
                r["tprob"] if plan.bet_type=="trio" else r["probs"], 7)
        if not got: continue
        legs, stakes, pu = got
        odds = r["tpo"] if pu.bet_type=="trio" else r["po"]
        if need_gate and not gate_pass(legs, stakes, odds): continue
        e=evaluate(r, legs, stakes, pu.bet_type, odds)
        e.update(key=r["key"], date=r["date"], type=r["type"], rtype=r["rtype"],
                 axis_sum=r["axis_sum"], plan=pu.key)
        out.append(e)
    return out

def summarize(rows, label=""):
    if not rows: return dict(label=label, n=0)
    bet=sum(x["bet"] for x in rows); pay=sum(x["payout"] for x in rows)
    hits=[x for x in rows if x["hit"]]
    disp=[x for x in rows if x["payout"]>=x["bet"]]
    big=[x for x in rows if x["payout"]>=100_000]
    days=len({x["date"] for x in rows})
    ratio=[x["payout"]/x["plan_pay_of_win"] for x in hits if x["plan_pay_of_win"]]
    return dict(label=label, n=len(rows), days=days, per_day=len(rows)/max(days,1),
                bet=bet, pay=pay, roi=pay/bet if bet else float("nan"),
                hit=len(hits)/len(rows), disp=len(disp)/len(rows),
                n_legs=float(np.mean([x["n"] for x in rows])),
                plan_pay=float(np.mean([x["mean_plan"] for x in rows])),
                med_payout_hit=float(np.median([x["payout"] for x in hits])) if hits else 0.0,
                real_over_plan=float(np.median(ratio)) if ratio else float("nan"),
                n100k=len(big), n100k_day=len(big)/max(days,1))

def boot_roi(rows, nb=2000, seed=0):
    """レース単位（=行単位、1レース1商品）bootstrap の ROI 95%CI。"""
    if not rows: return (float("nan"),)*2
    b=np.array([x["bet"] for x in rows],float); p=np.array([x["payout"] for x in rows],float)
    rng=np.random.default_rng(seed); n=len(rows); out=np.empty(nb)
    for i in range(nb):
        idx=rng.integers(0,n,n); out[i]=p[idx].sum()/b[idx].sum()
    return float(np.percentile(out,2.5)), float(np.percentile(out,97.5))

def boot_diff(rowsA, rowsB, nb=2000, seed=0):
    """同一レース対応比較の ΔROI（A-B）の95%CI。rowsA/B は key で対応。"""
    a={x["key"]:x for x in rowsA}; b={x["key"]:x for x in rowsB}
    ks=sorted(set(a)&set(b))
    if not ks: return (float("nan"),)*3
    ba=np.array([a[k]["bet"] for k in ks],float); pa=np.array([a[k]["payout"] for k in ks],float)
    bb=np.array([b[k]["bet"] for k in ks],float); pb=np.array([b[k]["payout"] for k in ks],float)
    d=pa.sum()/ba.sum()-pb.sum()/bb.sum()
    rng=np.random.default_rng(seed); n=len(ks); out=np.empty(nb)
    for i in range(nb):
        idx=rng.integers(0,n,n)
        out[i]=pa[idx].sum()/ba[idx].sum()-pb[idx].sum()/bb[idx].sum()
    return d, float(np.percentile(out,2.5)), float(np.percentile(out,97.5))

def fmt(s):
    if s.get("n",0)==0: return f"{s['label']}: n=0"
    return (f"{s['label']}: n={s['n']:5d} {s['per_day']:.2f}件/日 点数{s['n_legs']:.1f} "
            f"ROI {s['roi']*100:6.2f}% 的中 {s['hit']*100:5.2f}% 表示 {s['disp']*100:5.2f}% "
            f"計画払戻 {s['plan_pay']:>9,.0f} 実/計画 {s['real_over_plan']:.3f} "
            f"10万+ {s['n100k']:4d} ({s['n100k_day']:.3f}/日) 的中中央 {s['med_payout_hit']:>8,.0f}")
