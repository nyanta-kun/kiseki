#!/usr/bin/env python3
"""第5章 点数も組合せも変えない「並べ替えだけ」の腕（2026-09-10）。

現行の買い目を組んだあと、**各点を同じ3車の別の並びへ入れ替える**。
組合せは1つも変えないので、動くのは②順序違いだけ。ゲートを割ったら入れ替えない
（＝件数が1件も減らない）。ユーザー要望「点数を増やさず、外れている買い目を減らす」の
最も素直な形。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from line_order_arms import build_ctx, trio_probs, variant_probs, HIT  # noqa: E402
from line_order_build import AXIS_GATE_MIN, MIN_MEAN_PAYOUT, MIN_POINT_ODDS, _plan_for, load_rates  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, allocate, apply_line_swap, build_legs, mean_expected_payout)

PERMS, C3 = C.CANON, C.CANON3


def reorder(legs, prv, pod):
    """各点を同じ3車の別並びへ（`prv` が最大の並びを採る）。"""
    have = set(legs)
    out = []
    for c in legs:
        alts = [p for p in itertools.permutations(sorted(set(c)))
                if (p == c or p not in have) and pod.get(p)]
        best = max(alts, key=lambda p: prv.get(p, 0.0)) if alts else c
        if best != c:
            have.discard(c)
            have.add(best)
        out.append(best)
    return out


ARMS2 = [
    ("並替 λr1.3", 1.3, 1.0),
    ("並替 λr1.6", 1.6, 1.0),
    ("並替 λr1.9(較正値)", 1.9, 1.0),
    ("並替 λr2.0μr1.5(完全対称)", 2.0, 1.5),
    ("並替 λr3.0(逆を優遇)", 3.0, 1.0),
]


def main():
    z = C.board()
    a = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                           "BEHIND", "DAYI", "PO", "TRIO_PO", "TRIO_ODDS",
                           "TRIO_WIN", "TRIO_PAY", "WIN", "PAY", "DATE", "TYPE",
                           "RTYPE", "KEY", "AXIS_SUM")}
    rates = load_rates()
    tp = np.array([str(v) for v in a["TYPE"]])
    rt = np.array([str(v) for v in a["RTYPE"]])
    res = {w: {nm: [] for nm, *_ in ARMS2} for w in ("explore", "confirm")}
    for w in res:
        res[w]["現行"] = []
    # 組合せ内の並び当て（診断）
    perm_diag = {w: {"n": 0, "base": 0, "rev": 0, "mkt": 0, "line": 0}
                 for w in ("explore", "confirm")}
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = build_ctx(a, i, rates)
            if x is None or x.shape.type_label != tp[i]:
                continue
            pr = variant_probs(x.p0, x.lg, x.lp, 2.0, 1.5, 1.0, 1.0)
            pr3 = trio_probs(pr)
            # 売る商品（本番＝現行確率で決める。腕は買い目の並びだけ変える）
            def _mk(key, swap=True):
                plan = PLANS[key]
                trio = plan.bet_type == "trio"
                pod, prb = ((x.po_t3, pr3) if trio else (x.po_tf, pr))
                lg2 = build_legs(x.shape, plan, pod, prb)
                if not lg2:
                    return None
                st = allocate(lg2, pod, prb, plan)
                if not st:
                    return None
                if swap and not trio:
                    lg2, st = apply_line_swap(x.shape, plan, lg2, st, pod, prb)
                m = mean_expected_payout(st, pod)
                if m <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
                    return None
                return plan, trio, pod, prb, list(st), st
            trio_ok = (_mk("A_trio") is not None) if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok)
            if not key or float(a["AXIS_SUM"][i]) < AXIS_GATE_MIN.get(key, 0.0):
                continue
            got = _mk(key)
            if got is None:
                continue
            plan, trio, pod, prb, legs, st = got

            def _rec(legs2, st2):
                inv = float(sum(st2.values()))
                if trio:
                    pay = float(st2[x.win_t3] * x.odds_t3) if x.win_t3 in st2 else 0.0
                    inl = x.win_t3 in st2
                    seth = inl
                else:
                    pay = (float(st2[x.win_tf] / 100.0 * x.pay_tf * 100.0)
                           if x.win_tf in st2 else 0.0)
                    inl = x.win_tf in st2
                    seth = frozenset(x.win_tf) in {frozenset(c) for c in st2}
                return dict(i=i, date=x.date, plan=key, trio=trio, k=len(st2),
                            inv=inv, pay=pay, shown=pay >= inv, hit=pay > 0,
                            in_legs=inl, set_hit=seth)
            res[win]["現行"].append(_rec(legs, st))
            for nm, lr, mr in ARMS2:
                if trio:
                    res[win][nm].append(_rec(legs, st))
                    continue
                prv = variant_probs(x.p0, x.lg, x.lp, 2.0, 1.5, lr, mr)
                nl = reorder(legs, prv, pod)
                if set(nl) == set(legs):
                    res[win][nm].append(_rec(legs, st))
                    continue
                st2 = allocate(nl, pod, prb, plan)
                ok = (st2 and len(st2) == len(nl)
                      and mean_expected_payout(st2, pod) > MIN_MEAN_PAYOUT
                      and min(float(pod[c]) for c in st2) >= MIN_POINT_ODDS)
                res[win][nm].append(_rec(nl, st2) if ok else _rec(legs, st))

            # ── 診断: 当たり組合せを覆えたとき、6並びの中で当てられるか
            if not trio and frozenset(x.win_tf) in {frozenset(c) for c in legs}:
                s = sorted(set(x.win_tf))
                p6 = list(itertools.permutations(s))
                d = perm_diag[win]
                d["n"] += 1
                prv = variant_probs(x.p0, x.lg, x.lp, 2.0, 1.5, 1.9, 1.0)
                d["base"] += int(max(p6, key=lambda p: pr.get(p, 0.0)) == x.win_tf)
                d["rev"] += int(max(p6, key=lambda p: prv.get(p, 0.0)) == x.win_tf)
                d["mkt"] += int(min(p6, key=lambda p: pod.get(p, 9e9)) == x.win_tf)
                # ライン規則: 同ラインの2車は隊列順、単騎/別ラインは確率順
                def lkey(p):
                    v = pr.get(p, 0.0)
                    return v
                d["line"] += int(max(p6, key=lkey) == x.win_tf)
    pickle.dump({"res": res, "perm": perm_diag}, open("/tmp/lo/arms2.pkl", "wb"))
    print("→ /tmp/lo/arms2.pkl")


if __name__ == "__main__":
    main()
