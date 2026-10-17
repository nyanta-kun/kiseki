"""race × plan 反実仮想行列（2026-10-17・`docs/type_lab/plan_switch_2026_10_17.md`）。

実行: keirin/.venv/bin/python scripts/exp_type_lab/plan_switch/build_matrix.py（約3分・8並列）
前提: /tmp/race_type_board.npz（lineup_sim の台）。本番 type_lab の build + 入稿ゲートで全プランを全レースに当てる。"""
import os, sys, pickle, multiprocessing as mp
from pathlib import Path
import numpy as np
K = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(K)); sys.path.insert(0, str(K / "scripts/exp_type_lab"))
import lineup_sim as S
from src.type_lab import PLANS, sell_plans_for

MATRIX = os.environ.get("PLAN_SWITCH_MATRIX", "/tmp/plan_switch_matrix.pkl")
KEYS = ["A_hit", "A_trio", "A_ana", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_pay",
        "F_sign", "L_lead", "L_flat"]


def one(i):
    x = S.ctx(int(i))
    if x is None:
        return None
    sh = x.shape
    row = dict(i=int(i), key=x.key, date=x.date, rtype=x.rtype, cupg=x.cupg, rp_sd=x.rp_sd,
               type=sh.type_label, axis_sum=float(sh.axis_sum), pw_ent=float(sh.pw_ent),
               arare=getattr(sh, "arare", None), order=tuple(sh.order),
               pay_tf=x.pay_tf, odds_t3=x.odds_t3, plans={})
    for k in KEYS:
        got = S.build(x, PLANS[k])
        if not got:
            continue
        stakes, odds, used, mean = got
        trio = used.bet_type == "trio"
        prob = x.pr_t3 if trio else x.pr_tf
        inv, pay = S.settle(x, stakes, trio)
        row["plans"][k] = dict(used=used.key, gate=bool(S.gate_ok(stakes, odds, mean)),
                               inv=inv, pay=pay, n=len(stakes), mean=float(mean),
                               sp=float(sum(prob.get(c, 0.0) for c in stakes)),
                               minodds=float(min(odds[c] for c in stakes)))
    a = row["plans"].get("A_trio")
    trio_ok = bool(a and a["gate"]) if sh.type_label == "A" else None
    sell = sell_plans_for(sh.type_label, 7, x.rtype, pw_ent=sh.pw_ent, trio_ok=trio_ok)
    row["sell"] = sell[0].key if sell else None
    return row


if __name__ == "__main__":
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"])
    idx = np.flatnonzero(m)
    print(len(idx), flush=True)
    with mp.Pool(8) as p:
        rows = [r for r in p.imap(one, idx, chunksize=64) if r is not None]
    print(len(rows), flush=True)
    pickle.dump(rows, open(MATRIX, "wb"))
