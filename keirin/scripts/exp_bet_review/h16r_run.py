#!/usr/bin/env python3
"""H16 改訂版: 同じ総額で期待値が高い方（三連単 or 三連複）を組ごとに買う。判定用。

腕: cur / r2 / r3（予測オッズで判定）, 参考 r2o / r3o（最終オッズで判定=上限値・look-ahead）。
e_j = p_j*o_j（p=x.pr_tf=rank_7t3_blend_probs λμ後, o=予測オッズ）, e_tri(S)=P(S)*O_tri(S)（P=x.pr_t3, O_tri=1/Σ(1/o)）。
組ごとの賭け金合計 B_S は不変。r2: 現行の目(賭け金そのまま)の Σ s_j e_j と B_S e_tri を比べ高い方。
r3: e_j<e_tri(S) の目を外し残りへ B_S を予測オッズでダッチ再配分（残り無し→三連複）、その後 r2 と同じ比較。
"""
from __future__ import annotations
import pickle, sys, time
import numpy as np
import h16_run as H
from h16_run import *   # noqa
import lineup_arms as R

INFO2 = {}


def convert_rev(x, stakes, arm):
    oracle = arm.endswith("o")
    base = arm[:2]
    groups = {}
    for p, s in stakes.items():
        groups.setdefault(frozenset(p), []).append(p)
    if oracle:
        ftf, f3 = H.FINTF.get(x.key), H.FINT3.get(x.key)
        if ftf is None or f3 is None:
            return None, dict(ng=len(groups), conv=0, groups=[])
    out, det, nconv = {}, [], 0
    for g, ps in groups.items():
        B = sum(stakes[p] for p in ps)
        if g not in x.po_t3 or any(p not in x.po_tf for p in ps):
            for p in ps: out[p] = stakes[p]
            continue
        if oracle:
            if g not in f3 or any(p not in ftf for p in ps):
                for p in ps: out[p] = stakes[p]
                continue
            o = {p: ftf[p] for p in x.po_tf if frozenset(p) == g and p in ftf}
            e = {p: x.pr_tf[p] * o[p] for p in o}
            etri = x.pr_t3[g] * f3[g]
        else:
            e = {p: x.pr_tf[p] * x.po_tf[p] for p in x.po_tf if frozenset(p) == g}
            etri = x.pr_t3[g] * x.po_t3[g]
        cur_val = sum(stakes[p] * e[p] for p in ps)
        keep = {p: stakes[p] for p in ps}
        val_tf = cur_val
        if base == "r3":
            rem = [p for p in ps if e[p] >= etri]
            if rem:
                w = [1.0 / x.po_tf[p] for p in rem]
                u = H.TL._proportional(w, B // 100)
                keep = {p: k * 100 for p, k in zip(rem, u) if k > 0}
                val_tf = sum(keep[p] * e[p] for p in keep)
            else:
                keep, val_tf = {}, -1.0
        val_tri = B * etri
        det.append((tuple(sorted(g)), [(p, round(e[p], 4), stakes[p]) for p in ps], round(etri, 4), round(cur_val, 1), round(val_tf, 1), round(val_tri, 1)))
        if val_tri > val_tf or not keep:
            out[g] = B; nconv += 1
        else:
            out.update(keep)
    return out, dict(ng=len(groups), conv=nconv, groups=det)


def build_r(x, plan):
    odds, prob = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    got = H.TL.build_with_gate_fallback(x.shape, plan, odds, prob, 7,
                                        order_probs=None if plan.bet_type == "trio" else x.ord_tf)
    if not got:
        return None
    legs, stakes, used = got
    mean = H.TL.mean_expected_payout(stakes, odds)
    legs, stakes, _r = H.TL.add_upper_band(legs, stakes, used, odds, prob, 7)
    arm = H.ARM["v"]
    if arm != "cur" and used.key in H.HIT3 and used.bet_type == "trifecta":
        new, info = convert_rev(x, stakes, arm)
        INFO2[(arm, x.key, used.key)] = info
        if new is not None and info["conv"] > 0 or (new is not None and new != stakes):
            allo = {**x.po_tf, **x.po_t3}
            mean2 = sum(new[c] * float(allo[c]) for c in new) / len(new)
            return new, allo, used, mean2
    return stakes, odds, used, mean


def run_arm(name, ok, cache):
    H.ARM["v"] = name
    R.build = build_r
    R.settle = H.settle_mixed
    return R.run(name, {}, ok, cache)


if __name__ == "__main__":
    t0 = time.time()
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    H.load_final()
    z, idx = H.prep()
    if lim:
        idx = idx[:lim]
    cache = {int(i): H.S.ctx(int(i)) for i in idx}
    ok = [int(i) for i in idx if cache.get(int(i)) is not None]
    out = {"ndays": len({cache[i].date for i in ok})}
    for arm in ("cur", "r2", "r3", "r2o", "r3o"):
        out[arm] = run_arm(arm, ok, cache)
        print(arm, len(out[arm]), f"({time.time()-t0:.0f}s)", flush=True)
    out["info"] = INFO2
    pickle.dump(out, open(H.D / ("h16r_recs%s.pkl" % (f"_{lim}" if lim else "")), "wb"))
