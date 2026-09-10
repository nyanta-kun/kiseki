#!/usr/bin/env python3
"""オラクル天井（同じ k 点・同じ組合せ・並びだけ事後に最適化）。

「②順序違い」は定義上 **買った組合せの中に決着があるのに並びが違う**もの。
その1点を決着の並びへ差し替えれば必ず的中する。**点数も組合せも変えない**ので、
これが「並びの選び方」で到達しうる上限。入稿ゲートも通し直す。
"""
from __future__ import annotations

import pickle
import statistics as stat
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from line_order_arms import build_ctx, run, trio_probs, variant_probs, HIT  # noqa: E402
from line_order_build import AXIS_GATE_MIN, CARS, MIN_MEAN_PAYOUT, MIN_POINT_ODDS, _plan_for, load_rates  # noqa: E402
from src.type_lab import PLANS, allocate, mean_expected_payout  # noqa: E402


def main():
    z = C.board()
    a = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                           "BEHIND", "DAYI", "PO", "TRIO_PO", "TRIO_ODDS",
                           "TRIO_WIN", "TRIO_PAY", "WIN", "PAY", "DATE", "TYPE",
                           "RTYPE", "KEY", "AXIS_SUM")}
    rates = load_rates()
    tp = np.array([str(v) for v in a["TYPE"]])
    rt = np.array([str(v) for v in a["RTYPE"]])
    out = {}
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        base, orc = [], []
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = build_ctx(a, i, rates)
            if x is None or x.shape.type_label != tp[i]:
                continue
            pr = variant_probs(x.p0, x.lg, x.lp, 2.0, 1.5, 1.0, 1.0)
            pr3 = trio_probs(pr)
            trio_ok = (run(x, "A_trio", pr, pr3) is not None) if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok)
            if not key or float(a["AXIS_SUM"][i]) < AXIS_GATE_MIN.get(key, 0.0):
                continue
            plan = PLANS[key]
            trio = plan.bet_type == "trio"
            pod, prb = ((x.po_t3, pr3) if trio else (x.po_tf, pr))
            from src.type_lab import apply_line_swap, build_legs
            legs = build_legs(x.shape, plan, pod, prb)
            if not legs:
                continue
            st = allocate(legs, pod, prb, plan)
            if not st:
                continue
            if not trio:
                legs, st = apply_line_swap(x.shape, plan, legs, st, pod, prb)
            mean = mean_expected_payout(st, pod)
            if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
                continue
            inv = float(sum(st.values()))
            if trio:
                pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
            else:
                pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
            b = dict(i=i, date=x.date, plan=key, trio=trio, pay=pay, inv=inv,
                     shown=pay >= inv, k=len(st))
            base.append(b)
            # ── オラクル: 当たり組合せを覆う1点を決着の並びへ
            o = dict(b)
            if (not trio) and pay <= 0:
                w = x.win_tf
                cov = [c for c in st if frozenset(c) == frozenset(w)]
                if cov:
                    nl = [w if c == cov[0] else c for c in st]
                    if all(pod.get(c) for c in nl):
                        st2 = allocate(nl, pod, prb, plan)
                        if st2 and len(st2) == len(nl):
                            m2 = mean_expected_payout(st2, pod)
                            if m2 > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st2) >= MIN_POINT_ODDS:
                                p2 = float(st2[w] / 100.0 * x.pay_tf * 100.0)
                                i2 = float(sum(st2.values()))
                                o = dict(b, pay=p2, inv=i2, shown=p2 >= i2)
            orc.append(o)
        out[win] = (base, orc)
    pickle.dump(out, open("/tmp/lo/oracle.pkl", "wb"))
    # レポート
    for win in ("explore", "confirm"):
        base, orc = out[win]
        days = len({r["date"] for r in base})
        for nm, R in (("現行", base), ("並びオラクル", orc)):
            for grp, sel in (("全商品", lambda r: True),
                             ("三連単の当てにいく商品", lambda r: (not r["trio"]) and r["plan"] in HIT)):
                v = [r for r in R if sel(r)]
                sh = 100 * sum(r["shown"] for r in v) / len(v)
                pays = [r["pay"] for r in v if r["pay"] > 0]
                big = sum(1 for r in v if r["pay"] >= 100000) / days
                print(f"{win:8s}{nm:14s}{grp:24s} n={len(v):5d} 件/日={len(v)/days:5.2f} "
                      f"表示的中={sh:6.2f}% 払戻中央={stat.median(pays) if pays else 0:8.0f} "
                      f"10万+/日={big:.3f}")
        print()


if __name__ == "__main__":
    main()
