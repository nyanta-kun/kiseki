#!/usr/bin/env python3
"""ライン構造で順序を当て直す腕の評価（2026-09-10）。

腕はすべて **同じプラン・同じ点数**で、`rank_7t3_blend_probs` の
同ライン隣接ボーナスの形だけを変える。本番は

    P(x,y,z) ← P × λ^[y は x の直後] × μ^[z は y の直後]     λ=2.0 / μ=1.5

で、**「番手 → 先頭」（番手が差した並び）には何も掛かっていない**（`_line_next` は
`line_pos[b] == line_pos[a]+1` の一方向）。ここに逆向きボーナス λr / μr を入れる。

🔴 正規化は全体スカラーなので、ボーナス無しの確率へ後から掛けて正規化し直すのと
   本番の実装は数学的に同一（`assert` で1件突き合わせている）。
"""
from __future__ import annotations

import itertools
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
from line_order_build import (  # noqa: E402
    AXIS_GATE_MIN, CARS, MIN_MEAN_PAYOUT, MIN_POINT_ODDS, _plan_for, load_rates)
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, apply_line_swap, build_legs,
    mean_expected_payout, race_shape)
from src.strategy_wt import rank_7t3_blend_probs  # noqa: E402

PERMS, C3 = C.CANON, C.CANON3


def bonus_map(lg, lp, fwd, rev, cond=None, condw=None):
    """(x,y) -> 係数。cond(x,y) が True の並びだけ `condw` を使う。"""
    def f(a, b):
        if lg.get(a) in (None, "", "0") or lg.get(a) != lg.get(b):
            return 0
        pa, pb = lp.get(a, 0), lp.get(b, 0)
        if not pa or not pb:
            return 0
        if pb == pa + 1:
            return 1                      # 隊列順（先頭→番手）
        if pb == pa - 1:
            return -1                     # 逆（番手→先頭）
        return 0
    return f


def variant_probs(p0, lg, lp, lam, mu, lamr, mur, cond=None, lamr2=None):
    """ボーナス無し確率 `p0` へ係数を掛け直す。cond(x,y)=True の逆並びは lamr2。"""
    def adj(a, b):
        if lg.get(a) in (None, "", "0") or lg.get(a) != lg.get(b):
            return 0
        pa, pb = lp.get(a, 0), lp.get(b, 0)
        if not pa or not pb:
            return 0
        return 1 if pb == pa + 1 else (-1 if pb == pa - 1 else 0)
    out = {}
    for (x, y, z), v in p0.items():
        d1, d2 = adj(x, y), adj(y, z)
        if d1 == 1:
            v *= lam
        elif d1 == -1:
            v *= (lamr2(y, x) if (cond and lamr2) else lamr)
        if d2 == 1:
            v *= mu
        elif d2 == -1:
            v *= mur
        out[(x, y, z)] = v
    t = sum(out.values())
    return {k: v / t for k, v in out.items()} if t > 0 else p0


class Ctx:
    __slots__ = ("shape", "po_tf", "p0", "po_t3", "win_tf", "pay_tf", "win_t3",
                 "odds_t3", "date", "lg", "lp", "rp", "p3", "pw", "rates", "i")


def build_ctx(a, i, rates):
    p3 = {c: float(a["P3"][i][c - 1]) for c in CARS}
    pw = {c: float(a["PW"][i][c - 1]) for c in CARS}
    lg = {c: str(a["LG"][i][c - 1]) for c in CARS}
    lpr = a["A_line_pos"][i]
    lp = {c: (int(lpr[c - 1]) if np.isfinite(lpr[c - 1]) else 0) for c in CARS}
    stl = {c: str(a["ST"][i][c - 1]) for c in CARS}
    rp = {c: float(a["A_race_point"][i][c - 1]) for c in CARS}
    bh = {c: float(a["BEHIND"][i][c - 1]) for c in CARS}
    shape = race_shape(p3, lg, lp, stl, rp, bh, int(a["DAYI"][i]), win_probs=pw)
    if shape is None:
        return None
    po = {PERMS[t]: float(a["PO"][i][t]) for t in range(210)
          if np.isfinite(a["PO"][i][t]) and a["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    x = Ctx()
    x.i, x.shape, x.po_tf = i, shape, po
    x.p0 = rank_7t3_blend_probs(CARS, pw, p3)          # ボーナス無し
    x.po_t3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    x.win_tf = PERMS[int(a["WIN"][i])]
    x.pay_tf = float(a["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(a["TRIO_WIN"][i])])
    x.odds_t3 = float(a["TRIO_PAY"][i])
    x.date = str(a["DATE"][i])
    x.lg, x.lp, x.rp, x.p3, x.pw = lg, lp, rp, p3, pw
    x.rates = rates.get(str(a["KEY"][i]), {})
    return x


def trio_probs(pr):
    return {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
            for c in C3}


def run(x, key, pr, pr3, swap=True):
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, pr3) if trio else (x.po_tf, pr))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if swap and not trio:
        legs, st = apply_line_swap(x.shape, plan, legs, st, pod, prb)
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
        inl = x.win_t3 in st
        seth = inl
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
        inl = x.win_tf in st
        seth = frozenset(x.win_tf) in {frozenset(c) for c in st}
    inv = float(sum(st.values()))
    return dict(plan=key, trio=trio, k=len(st), inv=inv, pay=pay,
                shown=pay >= inv, hit=pay > 0, in_legs=inl, set_hit=seth)


# ───────────────────────── 腕の定義 ─────────────────────────
#: (名前, λ, μ, λr, μr, cond)  cond は逆並び (先頭L, 番手S) を受け「差す側」なら True。
def _c_rp(L, S, x):    return x.rp.get(S, 0) > x.rp.get(L, 0)
def _c_rate3(L, S, x):
    a, b = x.rates.get(S, (-1, -1, -1))[2], x.rates.get(L, (-1, -1, -1))[2]
    return a > b if min(a, b) >= 0 else False
def _c_rate1(L, S, x):
    a, b = x.rates.get(S, (-1, -1, -1))[0], x.rates.get(L, (-1, -1, -1))[0]
    return a > b if min(a, b) >= 0 else False


ARMS = [
    ("現行 λ2.0/μ1.5",          2.0, 1.5, 1.0, 1.0, None, None),
    ("ボーナス無し",             1.0, 1.0, 1.0, 1.0, None, None),
    ("逆 λr1.3",                2.0, 1.5, 1.3, 1.0, None, None),
    ("逆 λr1.6",                2.0, 1.5, 1.6, 1.0, None, None),
    ("逆 λr2.0(対称)",          2.0, 1.5, 2.0, 1.0, None, None),
    ("逆 λr1.6+μr1.3",          2.0, 1.5, 1.6, 1.3, None, None),
    ("完全対称 λ2.0μ1.5",       2.0, 1.5, 2.0, 1.5, None, None),
    ("逆λr 得点条件 2.2/1.1",   2.0, 1.5, 1.0, 1.0, _c_rp, (2.2, 1.1)),
    ("逆λr 3連対率条件 2.2/1.1", 2.0, 1.5, 1.0, 1.0, _c_rate3, (2.2, 1.1)),
    ("逆λr 勝率条件 2.2/1.1",   2.0, 1.5, 1.0, 1.0, _c_rate1, (2.2, 1.1)),
    ("λ強化 3.0/μ2.0",          3.0, 2.0, 1.0, 1.0, None, None),
]
HIT = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"}


def main():
    z = C.board()
    a = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                           "BEHIND", "DAYI", "PO", "TRIO_PO", "TRIO_ODDS",
                           "TRIO_WIN", "TRIO_PAY", "WIN", "PAY", "DATE", "TYPE",
                           "RTYPE", "KEY", "AXIS_SUM")}
    rates = load_rates()
    tp = np.array([str(v) for v in a["TYPE"]])
    rt = np.array([str(v) for v in a["RTYPE"]])
    res = {win: {nm: [] for nm, *_ in ARMS} for win in ("explore", "confirm")}
    calib = {win: [0.0, 0, 0] for win in ("explore", "confirm")}   # Σimplied, n, actual
    checked = [False]
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 4000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = build_ctx(a, i, rates)
            if x is None or x.shape.type_label != tp[i]:
                continue
            # ── 本番確率（突き合わせ用）
            prods = {}
            for nm, lam, mu, lr, mr, cond, cw in ARMS:
                if cond is None:
                    pr = variant_probs(x.p0, x.lg, x.lp, lam, mu, lr, mr)
                else:
                    f = (lambda L, S, _x=x, _c=cond, _w=cw:
                         _w[0] if _c(L, S, _x) else _w[1])
                    pr = variant_probs(x.p0, x.lg, x.lp, lam, mu, 1.0, mr,
                                       cond=True, lamr2=f)
                prods[nm] = pr
            if not checked[0]:
                ref = rank_7t3_blend_probs(CARS, x.pw, x.p3,
                                           line_group=x.lg, line_pos=x.lp)
                d = max(abs(ref[k] - prods["現行 λ2.0/μ1.5"][k]) for k in ref)
                assert d < 1e-12, d
                print(f"  [突き合わせ] 本番と一致 max|Δ|={d:.2e}")
                checked[0] = True
            # ── 頭対頭の較正（先頭×番手が1-2着を占めたレース）
            pr_base = prods["現行 λ2.0/μ1.5"]
            for ln in x.shape.lines:
                if len(ln) < 2:
                    continue
                L, S = ln[0], ln[1]
                f = sum(pr_base.get((L, S, c), 0.0) for c in CARS if c not in (L, S))
                rv = sum(pr_base.get((S, L, c), 0.0) for c in CARS if c not in (L, S))
                if f + rv <= 0:
                    continue
                w = x.win_tf
                if set(w[:2]) == {L, S}:
                    calib[win][0] += f / (f + rv)
                    calib[win][1] += 1
                    calib[win][2] += int(w[0] == L)
            # ── 腕ごとに商品を組む
            for nm, *_ in ARMS:
                pr = prods[nm]
                pr3 = trio_probs(pr)
                trio_ok = (run(x, "A_trio", pr, pr3) is not None) if tp[i] == "A" else False
                key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok)
                if not key or float(a["AXIS_SUM"][i]) < AXIS_GATE_MIN.get(key, 0.0):
                    continue
                r = run(x, key, pr, pr3)
                if r is None:
                    continue
                r["i"] = i
                r["date"] = x.date
                res[win][nm].append(r)
    pickle.dump({"res": res, "calib": calib}, open("/tmp/lo/arms.pkl", "wb"))
    print("→ /tmp/lo/arms.pkl")


if __name__ == "__main__":
    main()
