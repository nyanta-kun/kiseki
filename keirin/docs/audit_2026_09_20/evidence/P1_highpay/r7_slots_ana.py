"""① 5枠の T 構成（現行 3×15万+2×40万 ほか）② A_ana の帯を絞る ③ 実売の整合（T_upset 込み）"""
import sys, numpy as np, pandas as pd
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
from src import type_lab as TL
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
races=lib.get_races()
def bcd(r): return r["type"] in ("B","C","D")

print("### ① 1商品あたりの確率（型B/C/D・現行レシピ）")
print(f"{'窓':<6}{'T':>9}{'n':>6}{'P(的中)':>9}{'P(10万+)':>10}{'P(20万+)':>10}{'P(30万+)':>10}{'ROI%':>8}")
tab={}
for wn,ws in [("探索",lib.window(races,lib.EXPLORE)),("確認",lib.window(races,lib.CONFIRM))]:
    for T in [50_000,75_000,100_000,150_000,200_000,300_000,400_000]:
        rows=design.build(ws,T,pop=bcd, bust=(T>=400_000))
        n=len(rows); p100=sum(1 for x in rows if x["payout"]>=100_000)/n
        p200=sum(1 for x in rows if x["payout"]>=200_000)/n
        p300=sum(1 for x in rows if x["payout"]>=300_000)/n
        roi=sum(x["payout"] for x in rows)/sum(x["bet"] for x in rows)
        hit=sum(x["hit"] for x in rows)/n
        tab[(wn,T)]=(p100,p200,p300,roi)
        print(f"{wn:<6}{T:>9,}{n:>6}{hit*100:>8.2f}%{p100*100:>9.2f}%{p200*100:>9.2f}%{p300*100:>9.2f}%{roi*100:>8.2f}")

print("\n### ② 5枠/日の構成別 期待値（上の1商品確率 ×枠数。bust は 40万のみ）")
MIX={"現行 3×15万+2×40万":[150_000]*3+[400_000]*2,
     "全部15万":[150_000]*5, "全部20万":[200_000]*5,
     "全部10万":[100_000]*5, "4×15万+1×40万":[150_000]*4+[400_000],
     "3×15万+2×30万":[150_000]*3+[300_000]*2}
print(f"{'構成':<22}{'探索 10万+/日':>14}{'20万+/日':>10}{'ROI%':>8} | {'確認 10万+/日':>14}{'20万+/日':>10}{'ROI%':>8}")
for nm,mix in MIX.items():
    o=[]
    for wn in ["探索","確認"]:
        p1=sum(tab[(wn,t)][0] for t in mix); p2=sum(tab[(wn,t)][1] for t in mix)
        roi=np.mean([tab[(wn,t)][3] for t in mix])
        o.append((p1,p2,roi))
    print(f"{nm:<22}{o[0][0]:>14.3f}{o[0][1]:>10.3f}{o[0][2]*100:>8.2f} | "
          f"{o[1][0]:>14.3f}{o[1][1]:>10.3f}{o[1][2]*100:>8.2f}")

print("\n### ③ A_ana（軸1を外す・上位5点ダッチ）の帯を絞る")
def ana(ws, max_odds=0.0, max_legs=5):
    out=[]
    for r in ws:
        if r["type"]!="A": continue
        if r["shape"].pw_ent < TL.ANA_PW_ENT_MIN: continue
        pool=set(r["shape"].order[1:])
        cand=[k for k,v in r["po"].items() if v>0 and len(set(k))==3 and set(k)<=pool
              and (not max_odds or float(v)<=max_odds)]
        cand.sort(key=lambda k: -float(r["probs"].get(k,0.0)))
        legs=cand[:max_legs]
        if len(legs)<2: continue
        st=design.alloc(legs, r["po"], r["probs"], "dutch")
        if not st: continue
        mp=sum(st[c]*float(r["po"][c]) for c in st)/len(st)
        if mp<=20000 or min(float(r["po"][c]) for c in st)<2.0: continue
        out.append(design.score(r,st,"trifecta",r["po"]))
    return out
print(f"{'窓':<6}{'腕':<16}{'n':>6}{'点':>4}{'的中%':>7}{'ROI%':>8}{'ROI CI':>18}{'計画払戻':>10}{'10万+/日':>9}")
for wn,ws in [("探索",lib.window(races,lib.EXPLORE)),("確認",lib.window(races,lib.CONFIRM))]:
    base=None
    for nm,kw in [("現行(上限なし)",{}),("max_odds=300",dict(max_odds=300.0)),
                  ("max_odds=200",dict(max_odds=200.0)),("max_odds=100",dict(max_odds=100.0)),
                  ("上位3点",dict(max_legs=3))]:
        rows=ana(ws,**kw)
        if base is None: base=rows
        s=E.summarize(rows,nm); lo,hi=E.boot_roi(rows)
        print(f"{wn:<6}{nm:<16}{s['n']:>6}{s['n_legs']:>4.1f}{s['hit']*100:>7.2f}{s['roi']*100:>8.2f}"
              f"  [{lo*100:>5.1f},{hi*100:>6.1f}]{s['plan_pay']:>10,.0f}{s['n100k_day']:>9.3f}")
