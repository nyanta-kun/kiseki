"""計画払戻 T のトレードオフ曲線（現行レシピのまま T だけ動かす）。"""
import sys, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
GATE_MIN={"A_hit":1.596,"B_hit":1.539,"C_hit":1.500,"D_hit":1.304,"E_hit":1.284,"F_hit":1.246}
races=lib.get_races()
def bcd(r): return r["type"] in ("B","C","D")
def bcd_fail(r):
    return bcd(r) and r["axis_sum"] < GATE_MIN[f"{r['type']}_hit"]
Ts=[50_000,75_000,100_000,150_000,200_000,300_000,400_000]
for wn,ws in [("探索 2024-07〜2025-12",lib.window(races,lib.EXPLORE)),
              ("確認 2026-01〜07-15",lib.window(races,lib.CONFIRM))]:
    for popname,pop in [("型B/C/D 全部",bcd),("型B/C/D∧本線が軸信頼ゲート落ち",bcd_fail)]:
        print(f"\n===== {wn} / 母集団={popname} =====")
        print(f"{'計画払戻T':>10}{'n':>6}{'点数':>6}{'的中%':>7}{'表示%':>7}{'ROI%':>8}{'ROI 95%CI':>18}"
              f"{'10万+/日':>9}{'20万+/日':>9}{'的中中央':>10}{'実/計画':>8}")
        for T in Ts:
            rows=design.build(ws,T,pop=pop)
            if not rows: continue
            s=E.summarize(rows,str(T)); lo,hi=E.boot_roi(rows)
            days=s["days"]
            n20=sum(1 for x in rows if x["payout"]>=200_000)
            print(f"{T:>10,}{s['n']:>6}{s['n_legs']:>6.1f}{s['hit']*100:>7.2f}{s['disp']*100:>7.2f}"
                  f"{s['roi']*100:>8.2f}  [{lo*100:>6.1f},{hi*100:>6.1f}]"
                  f"{s['n100k_day']:>9.3f}{n20/days:>9.3f}{s['med_payout_hit']:>10,.0f}{s['real_over_plan']:>8.3f}")
