import sys, collections, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, engine as E
from src import type_lab as TL
GATE_MIN={"A_hit":1.596,"A_trio":1.528,"B_hit":1.539,"C_hit":1.500,"D_hit":1.304,
          "E_hit":1.284,"F_hit":1.246,"F_sign":1.267}
races=lib.get_races()
W={"探索 2024-07〜2025-12":lib.window(races,lib.EXPLORE),
   "確認 2026-01〜07-15":lib.window(races,lib.CONFIRM)}
HIGH=["B_sign","C_sign","D_sign","B_big","C_big","D_big","F_sign","A_ana","F_pay"]
MAIN=["A_hit","B_hit","C_hit","D_hit","E_hit","F_hit"]
def axis_fail(r):
    fl=GATE_MIN.get(f"{r['type']}_hit")
    return fl is not None and r["axis_sum"]<fl
for wn,ws in W.items():
    print(f"\n########## {wn}  ({len(ws)}R) ##########")
    for pk in HIGH+MAIN:
        pl=TL.PLANS[pk]
        rows=E.run_plan(ws,pl)
        s=E.summarize(rows,pk); lo,hi=E.boot_roi(rows)
        print(E.fmt(s)+f" CI[{lo*100:.1f},{hi*100:.1f}]")
    print("--- 高額枠を『実際に置かれる母集団』（本線が軸信頼ゲート落ちのレース）で ---")
    for pk in ["B_sign","C_sign","D_sign","B_big","C_big","D_big"]:
        pl=TL.PLANS[pk]
        rows=E.run_plan(ws,pl,pop_filter=axis_fail)
        s=E.summarize(rows,pk+"@gate落ち"); lo,hi=E.boot_roi(rows)
        print(E.fmt(s)+f" CI[{lo*100:.1f},{hi*100:.1f}]")
