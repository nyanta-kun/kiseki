"""ROI の分解: ①帯（longshot bias）②予測↔確定のずれ ③帯内でのモデル選別 ④置き場所。

ダッチ配分では ROI = Σ w_i r_i / Σ w_i  （w_i = 1/予測オッズ, r_i = 1{当}×確定オッズ）
なので「買った点の帯の無情報回収率を賭け金で加重平均した値」が ①の寄与、
実測との差が ③（帯内でのモデルの選別）になる。
"""
import sys, numpy as np, collections
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, engine as E
from src import type_lab as TL
BND=[(0,10),(10,15),(15,30),(30,60),(60,100),(100,200),(200,300),(300,600),(600,1e9)]
def bi(o):
    for j,(lo,hi) in enumerate(BND):
        if lo<=o<hi: return j
    return len(BND)-1

def band_baseline(ws):
    num=np.zeros(len(BND)); den=np.zeros(len(BND))
    for r in ws:
        po=r["po"]; win=r["win"]; pay=r["pay"]/100.0
        if win is None: continue
        for k,o in po.items():
            j=bi(float(o)); den[j]+=1
            if k==win: num[j]+=pay
    return np.where(den>0, num/np.maximum(den,1), np.nan), den

def plan_rows_with_legs(ws, plan, pop=None):
    out=[]
    for r in ws:
        if plan.type_label not in ("*", r["type"]): continue
        if pop and not pop(r): continue
        odds = r["tpo"] if plan.bet_type=="trio" else r["po"]
        prob = r["tprob"] if plan.bet_type=="trio" else r["probs"]
        got=TL.build_with_gate_fallback(r["shape"],plan,odds,prob,7)
        if not got: continue
        legs,st,pu=got
        o2 = r["tpo"] if pu.bet_type=="trio" else r["po"]
        if not E.gate_pass(legs,st,o2): continue
        out.append((r,legs,st,pu,o2))
    return out

races=lib.get_races()
GATE_MIN={"B_hit":1.539,"C_hit":1.500,"D_hit":1.304}
for wn,ws in [("探索 2024-07〜2025-12",lib.window(races,lib.EXPLORE)),
              ("確認 2026-01〜07-15",lib.window(races,lib.CONFIRM))]:
    bb,_=band_baseline(ws)
    print(f"\n===== {wn} =====")
    print(f"{'plan':<8}{'n':>6}{'実ROI':>8}{'帯だけの期待':>12}{'差(帯内選別)':>12}  帯構成(賭け金比)")
    for pk in ["B_sign","C_sign","D_sign","B_big","C_big","D_big","F_sign","A_ana","F_pay",
               "A_hit","C_hit","F_hit"]:
        pl=TL.PLANS[pk]
        rows=plan_rows_with_legs(ws,pl)
        if not rows: continue
        bet=0.0; pay=0.0; wsum=np.zeros(len(BND))
        for r,legs,st,pu,odds in rows:
            bet+=sum(st.values())
            if pu.bet_type=="trio":
                if r["twin"] in st: pay+=st[r["twin"]]*r["tpay"]
            else:
                if r["win"] in st: pay+=st[r["win"]]/100.0*r["pay"]
            if pu.bet_type!="trio":
                for c in legs: wsum[bi(float(odds[c]))]+=st[c]
        roi=pay/bet
        if wsum.sum()>0:
            exp=np.nansum(wsum*np.nan_to_num(bb))/wsum.sum()
            comp=" ".join(f"{BND[j][0]}-{int(BND[j][1]) if BND[j][1]<1e8 else 0}:{wsum[j]/wsum.sum()*100:.0f}%"
                          for j in range(len(BND)) if wsum[j]/wsum.sum()>=0.03)
        else:
            exp=float("nan"); comp="(三連複)"
        print(f"{pk:<8}{len(rows):>6}{roi*100:>7.2f}%{exp*100:>11.2f}%{(roi-exp)*100:>+11.2f}pt  {comp}")
    print("--- 置き場所（C_sign を母集団で切る） ---")
    pl=TL.PLANS["C_sign"]
    for nm,pop in [("全C型",None),
                   ("C_hit が軸信頼ゲート落ち",lambda r: r["axis_sum"]<GATE_MIN["C_hit"]),
                   ("C_hit がゲート通過",lambda r: r["axis_sum"]>=GATE_MIN["C_hit"]),
                   ("決勝系",lambda r: r["rtype"] in ("決勝","特選","S級特選","初特選")),
                   ("予選",lambda r: r["rtype"]=="予選")]:
        rows=plan_rows_with_legs(ws,pl,pop)
        rr=[dict(bet=sum(st.values()),
                 payout=(st[r["win"]]/100.0*r["pay"]) if r["win"] in st else 0.0,
                 hit=r["win"] in st, key=r["key"], date=r["date"], n=len(legs),
                 mean_plan=TL.mean_expected_payout(st,odds),
                 plan_pay_of_win=(st[r["win"]]*float(odds[r["win"]])) if r["win"] in st else None)
            for r,legs,st,pu,odds in rows]
        s=E.summarize(rr,f"C_sign@{nm}"); lo,hi=E.boot_roi(rr)
        print("   "+E.fmt(s)+f" CI[{lo*100:.1f},{hi*100:.1f}]")
