#!/usr/bin/env python3
"""H01 Step1: 現行 vs LB 適用（同一レース・同一台・vintage 予測オッズ）。

事前登録どおり β は Step0 の CI 下限（0.01 刻みで切り捨て）: k1=0.25, k2=0（掛けない）, k3=0.20。固定・掃引しない。
LB は 三連単 P の (先頭→番手) が 1-2 着の目すべてに (1+β_k) を掛け、レース内で再正規化したもの。
対象プランは A/B/C/E/F_hit（三連単・GATE_FALLBACK の同キー含む）。A_ana / A_trio / D_hit / *_sign / *_big と
ord_tf（並べ替え用）・pr_t3 は元のまま。点数・帯・入稿ゲート・軸信頼ゲート(p20)・日次上限・配分は本番関数のまま。
"""
from __future__ import annotations
import json, pickle, random, sys, time
from collections import defaultdict
import numpy as np
from h01_common import *   # noqa
from h01_step0 import classify
import lineup_sim as S
import lineup_arms as R
import src.type_lab as TL

BETA = {"k1": 0.25, "k2": 0.0, "k3": 0.20}
LB_PLANS = frozenset({"A_hit", "B_hit", "C_hit", "E_hit", "F_hit"})
ARM = {"v": "cur"}
CNT = {"ls_calls": 0, "ls_fire": 0}


def prep():
    b = load_board_2025()
    S._Z = {k: b[k] for k in S._NEED}      # lineup_sim.board() を差し替える（PO は vintage）
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"])
    idx = np.flatnonzero(m)
    return z, idx


def make_ctx(i, z):
    x = S.ctx(int(i))
    if x is None:
        return None
    ev, _ = classify(z["LG"][i], z["A_line_pos"][i], z["ST"][i], z["A_race_point"][i])
    mult = {}
    for lead, ban, k in ev:
        if BETA[k] > 0:
            for zc in range(1, 8):
                if zc not in (lead, ban):
                    mult[(lead, ban, zc)] = 1.0 + BETA[k]
    x.lb_events = [(a, c, k) for a, c, k in ev if BETA[k] > 0]
    pr = {kk: v * mult.get(kk, 1.0) for kk, v in x.pr_tf.items()}
    tot = sum(pr.values())
    x.pr_tf_lb = {kk: v / tot for kk, v in pr.items()}
    return x


def build_dispatch(x, plan, plans_override=None):
    use_lb = ARM["v"] == "lb" and plan.key in LB_PLANS and plan.bet_type == "trifecta"
    prob_tf = x.pr_tf_lb if use_lb else x.pr_tf
    odds, prob = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, prob_tf))
    got = TL.build_with_gate_fallback(
        x.shape, plan, odds, prob, 7,
        order_probs=None if plan.bet_type == "trio" else x.ord_tf)
    if not got:
        return None
    legs, stakes, used = got
    mean = TL.mean_expected_payout(stakes, odds)
    legs, stakes, _r = TL.add_upper_band(legs, stakes, used, odds, prob, 7)
    return stakes, odds, used, mean


_ls0 = TL.apply_line_swap


def _ls_wrap(shape, plan, legs, stakes, *a, **kw):
    out = _ls0(shape, plan, legs, stakes, *a, **kw)
    CNT["ls_calls"] += 1
    if [tuple(c) for c in out[0]] != [tuple(c) for c in legs]:
        CNT["ls_fire"] += 1
    return out


def run_arm(name, ok, cache):
    ARM["v"] = name
    CNT["ls_calls"] = CNT["ls_fire"] = 0
    TL.apply_line_swap = _ls_wrap
    R.build = build_dispatch
    try:
        recs = R.run(name, {}, ok, cache)
    finally:
        TL.apply_line_swap = _ls0
    return recs, dict(CNT)


if __name__ == "__main__":
    t0 = time.time()
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    z, idx = prep()
    if lim:
        idx = idx[:lim]
    cache = {int(i): make_ctx(i, z) for i in idx}
    ok = [int(i) for i in idx if cache.get(int(i)) is not None]
    ndays = len({cache[i].date for i in ok})
    print(f"対象 {len(idx)}R 組めた {len(ok)}R / {ndays}日  ({time.time()-t0:.0f}s)", flush=True)
    out = {}
    for arm in ("cur", "lb"):
        recs, cnt = run_arm(arm, ok, cache)
        out[arm] = dict(recs=recs, cnt=cnt)
        print(arm, len(recs), cnt, f"({time.time()-t0:.0f}s)", flush=True)
    out["ndays"] = ndays
    out["lb_events"] = {cache[i].key: cache[i].lb_events for i in ok if cache[i].lb_events}
    out["cheap"] = {}
    for i in ok:
        x = cache[i]
        out["cheap"][x.key] = (TL.cheap_share(x.po_tf, x.pr_tf), TL.cheap_share(x.po_tf, x.pr_tf_lb))
    pickle.dump(out, open(D / ("h01_step1_recs%s.pkl" % (f"_{lim}" if lim else "")), "wb"))
