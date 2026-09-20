import sys
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, picks
from src import type_lab as TL
races=lib.get_races()
w=lib.window(races, lib.CONFIRM)
for pk in ["C_sign","C_big","F_sign","A_ana","F_pay"]:
    pl=TL.PLANS[pk]
    shown=0
    for r in w:
        if r["type"]!=pl.type_label: continue
        got=picks.build_pick(r,pl)
        if not got: continue
        legs,st,pu=got
        odds=r["tpo"] if pu.bet_type=="trio" else r["po"]
        s=sum(1/float(odds[c]) for c in legs)
        print(f"--- {pk} {r['key']} {r['date']} type={r['type']} rtype={r['rtype']} axis1={r['shape'].order[0]}")
        for c in legs:
            print(f"    {c} 賭金{st[c]:>6}円 予測{float(odds[c]):>8.1f}倍 → 想定払戻 {st[c]*float(odds[c]):>9,.0f}円  prob={r['probs'].get(c,0):.5f}")
        print(f"    計 {sum(st.values())}円  Σ(1/予測)={s:.4f}  予算/Σ={10000/s:,.0f}円  平均想定払戻={TL.mean_expected_payout(st,odds):,.0f}円")
        print(f"    的中目={r['win']} 確定払戻(円/100円)={r['pay']:.0f}")
        shown+=1
        if shown>=2: break
