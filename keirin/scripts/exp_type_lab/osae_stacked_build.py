#!/usr/bin/env python3
"""押さえ目を **τ適応 + λr の上に積んだ本番経路**で測るための台（2026-09-11）。

`order_swap_build.py` の拡張。違いは3つ:

- `apply_osae` を包んで **候補数・発動・足した点の予測オッズ・ゲートで見送り・
  押さえで当たったか（と確定オッズ）** を拾う。
- **無作為対照**をレースごとにその場で作る（本案が実際に足した点数と同数を、
  買っていない目から無作為に。帯あり / 帯なしの2種 × 20seed）。
- 腕は3つ:

    ⓪ 現行          `--repo <main>`
    ② τ適応+λr      `--repo <worktree> --no-osae`（`OSAE_PLANS` を空に）
    ③ 3つ全部       `--repo <worktree>`

🔴 `RaceShape` には `lines` / `line_pair` を必ず渡す（`apply_line_swap` が発動する）。
   `A_line_pos` は float32 なので `int()` してから渡すこと。

    PYTHONPATH=<repo> python osae_stacked_build.py --repo <repo> --out <pkl>
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import pickle
import sys
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True, help="keirin リポジトリのルート")
ap.add_argument("--out", required=True)
ap.add_argument("--no-order-swap", action="store_true",
                help="`ORDER_SWAP_PLANS` を空にして λr を無効化する")
ap.add_argument("--no-osae", action="store_true",
                help="`OSAE_PLANS` を空にして押さえ目を無効化する（腕②）")
ap.add_argument("--osae-plans", default=None,
                help="カンマ区切りでプランを指定（切り分け用）")
ap.add_argument("--nseed", type=int, default=20, help="無作為対照の seed 数")
ap.add_argument("--no-control", action="store_true", help="無作為対照を作らない")
ap.add_argument("--limit", type=int, default=0, help="各窓の先頭N件だけ（煙試験）")
ap.add_argument("--no-tau", action="store_true",
                help="`tau_adaptive` を全プランで外す（重なりの切り分け用）")
args = ap.parse_args()

REPO = Path(args.repo).resolve()
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs             # noqa: E402

HAS_ORDER = hasattr(TL, "apply_order_swap")
HAS_OSAE = hasattr(TL, "apply_osae")
if HAS_ORDER:
    from src.strategy_wt import rank_7t3_order_swap_probs    # noqa: E402
    if args.no_order_swap:
        TL.ORDER_SWAP_PLANS = frozenset()
if HAS_OSAE:
    if args.no_osae:
        TL.OSAE_PLANS = frozenset()
    elif args.osae_plans is not None:
        TL.OSAE_PLANS = frozenset(k for k in args.osae_plans.split(",") if k)

if args.no_tau and hasattr(TL.PLANS["C_hit"], "tau_adaptive"):
    from dataclasses import replace as _rep                  # noqa: E402
    TL.PLANS = {k: _rep(v, tau_adaptive=False) for k, v in TL.PLANS.items()}
    TL.GATE_FALLBACK = {k: tuple(_rep(p_, tau_adaptive=False) for p_ in v)
                        for k, v in TL.GATE_FALLBACK.items()}

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                    # type: ignore[union-attr]

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0
PERMS = C.CANON
C3 = C.CANON3
NSEED = 0 if (args.no_control or not HAS_OSAE or args.no_osae) else args.nseed
OSAE_LO = float(getattr(TL, "OSAE_MIN_PRED_ODDS", 125.0))
OSAE_ST = int(getattr(TL, "OSAE_STAKE", 100))

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

_ST: dict = {}
_orig_line = TL.apply_line_swap


def _w_line(shape, plan, legs, stakes, pred_odds, probs,
            min_mean_payout=TL.MIN_MEAN_PAYOUT):
    out = _orig_line(shape, plan, legs, stakes, pred_odds, probs, min_mean_payout)
    _ST["line_hit"] = [tuple(c) for c in out[0]] != [tuple(c) for c in legs]
    return out


TL.apply_line_swap = _w_line

if HAS_ORDER:
    _orig_order = TL.apply_order_swap

    def _w_order(plan, legs, stakes, pred_odds, order_probs,
                 min_mean_payout=TL.MIN_MEAN_PAYOUT):
        out = _orig_order(plan, legs, stakes, pred_odds, order_probs, min_mean_payout)
        a = [tuple(c) for c in legs]
        b = [tuple(c) for c in out[0]]
        _ST["ochg"] = sum(1 for x, y in zip(a, b) if x != y)
        return out

    TL.apply_order_swap = _w_order


def _gate(st, pod) -> bool:
    if not st:
        return False
    if TL.mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return False
    return min(float(pod[c]) for c in st) >= MIN_POINT_ODDS


def _ctrl_stakes(plan, legs, stakes, pred_odds, probs, k, rng, band):
    """無作為対照。買っていない目から k 点（band=True なら予測 `OSAE_LO` 倍以上）。"""
    have = {tuple(c) for c in legs}
    pool = [c for c, o in pred_odds.items()
            if c not in have and float(o) > 0 and (not band or float(o) >= OSAE_LO)]
    if len(pool) < k:
        return None
    add = [pool[int(j)] for j in rng.choice(len(pool), size=k, replace=False)]
    st = TL.allocate(list(legs), pred_odds, probs, plan,
                     budget=TL.BUDGET - OSAE_ST * k)
    if not st or len(st) != len(legs):
        return None
    out = dict(st)
    for c in add:
        out[c] = OSAE_ST
    if not _gate(out, pred_odds):
        return None
    return out, add


if HAS_OSAE:
    _orig_osae = TL.apply_osae

    def _w_osae(shape, plan, legs, stakes, pred_odds, probs,
                min_mean_payout=TL.MIN_MEAN_PAYOUT):
        base = [tuple(c) for c in legs]
        cand_n = 0
        elig = (plan.key in TL.OSAE_PLANS and plan.bet_type == "trifecta"
                and bool(legs) and len(shape.order) >= 3)
        if elig:
            have = set(base)
            a1, a2 = shape.order[0], shape.order[1]
            for first, second in ((a1, a2), (a2, a1)):
                for third in shape.order[2:]:
                    kk = (first, second, third)
                    if kk in have:
                        continue
                    o = pred_odds.get(kk)
                    if o and float(o) >= OSAE_LO:
                        cand_n += 1
        out = _orig_osae(shape, plan, legs, stakes, pred_odds, probs, min_mean_payout)
        add = [tuple(c) for c in out[0] if tuple(c) not in base]
        _ST["ocand_n"] = cand_n
        _ST["osae_n"] = len(add)
        _ST["osae_legs"] = add
        _ST["osae_pred"] = [float(pred_odds.get(c, 0.0)) for c in add]
        # 候補があるのに足せなかった理由を2つに割る:
        #   ① ゲート（平均想定払戻が2万を割る）→ `osae_gated`
        #   ② 残予算で床が置けない（`allocate` が None）→ `osae_nofit`
        _ST["osae_gated"] = False
        _ST["osae_nofit"] = False
        if cand_n > 0 and not add:
            ung = _orig_osae(shape, plan, legs, stakes, pred_odds, probs, -1e18)
            if len(ung[0]) > len(base):
                _ST["osae_gated"] = True
            else:
                _ST["osae_nofit"] = True
        # ── 無作為対照（本案と同数）──
        _ST["ctrl"] = {}
        if NSEED and add:
            for band, tag in ((True, "band"), (False, "free")):
                lst = []
                for sd in range(NSEED):
                    rng = np.random.default_rng(
                        (sd + 1) * 1_000_003 + int(_ST.get("race_i", 0)))
                    got = _ctrl_stakes(plan, base, stakes, pred_odds, probs,
                                       len(add), rng, band)
                    lst.append(got[0] if got else None)
                _ST["ctrl"][tag] = lst
        return out

    TL.apply_osae = _w_osae


class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "or_tf", "po_t3", "pr_t3",
                 "win_tf", "pay_tf", "win_t3", "odds_t3", "date", "rtype")


def ctx(i: int) -> Ctx | None:
    cars = list(range(1, 8))
    p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
    pw = {c: float(Z["PW"][i][c - 1]) for c in cars}
    lg = {c: str(Z["LG"][i][c - 1]) for c in cars}
    lp = {}
    for c in cars:
        v = Z["A_line_pos"][i][c - 1]
        lp[c] = int(v) if np.isfinite(v) else None
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    orp = (rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
           if HAS_ORDER else None)
    po = {PERMS[t]: float(Z["PO"][i][t]) for t in range(210)
          if np.isfinite(Z["PO"][i][t]) and Z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    lines = TL._lines_of(lg, lp)
    x = Ctx()
    x.shape = TL.RaceShape(
        str(Z["TYPE"][i]), float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
        float(Z["GAP"][i]), float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
        order, TL.win_entropy(pw), lines, TL._strongest_pair(lines, p3))
    x.po_tf, x.pr_tf, x.or_tf = po, pr, orp
    x.po_t3 = {frozenset(c): float(Z["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(Z["TRIO_PO"][i][j]) and Z["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
               for c in C3}
    x.win_tf = PERMS[int(Z["WIN"][i])]
    x.pay_tf = float(Z["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(Z["TRIO_WIN"][i])])
    x.odds_t3 = float(Z["TRIO_PAY"][i])
    x.date = str(Z["DATE"][i])
    x.rtype = str(Z["RTYPE"][i])
    return x


def build(x: Ctx, plan, race_i: int = 0) -> dict | None:
    trio = plan.bet_type == "trio"
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    _ST.clear()
    _ST.update(line_hit=False, ochg=0, ocand_n=0, osae_n=0, osae_legs=[],
               osae_pred=[], osae_gated=False, osae_nofit=False,
               ctrl={}, race_i=race_i)
    if HAS_ORDER:
        got = TL.build_with_gate_fallback(
            x.shape, plan, pod, prb, 7, order_probs=None if trio else x.or_tf)
    else:
        got = TL.build_with_gate_fallback(x.shape, plan, pod, prb, 7)
    if not got:
        return None
    legs, st, pl = got
    mean = float(TL.mean_expected_payout(st, pod))
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
        sethit = x.win_t3 in st
        exact = sethit
    else:
        pay = float(st[x.win_tf] * x.pay_tf) if x.win_tf in st else 0.0
        sets = {frozenset(c) for c in st}
        sethit = frozenset(x.win_tf) in sets
        exact = x.win_tf in st
    osae_legs = _ST.get("osae_legs") or []
    osae_win = (not trio) and x.win_tf in set(osae_legs)
    ctrl = {}
    for tag, lst in (_ST.get("ctrl") or {}).items():
        cp, ci = [], []
        for cst in lst:
            if cst is None:
                cp.append(None)
                ci.append(None)
            else:
                cp.append(float(cst[x.win_tf] * x.pay_tf) if x.win_tf in cst else 0.0)
                ci.append(float(sum(cst.values())))
        ctrl[tag] = (cp, ci)
    return dict(key=pl.key, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate), swapped=bool(_ST.get("line_hit")),
                ochg=int(_ST.get("ochg") or 0),
                ocand_n=int(_ST.get("ocand_n") or 0),
                osae_n=len(osae_legs), osae_pred=list(_ST.get("osae_pred") or []),
                osae_gated=bool(_ST.get("osae_gated")),
                osae_nofit=bool(_ST.get("osae_nofit")),
                osae_win=bool(osae_win),
                fin_odds=float(x.pay_tf) if not trio else float(x.odds_t3),
                ctrl=ctrl,
                sethit=bool(sethit), exact=bool(exact),
                min_odds=float(pl.min_odds))


def main() -> None:
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if str(Z["TYPE"][int(i)]) in "ABCDEF"]
        if args.limit:
            idx = idx[:args.limit]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = x.shape.type_label
            trio_ok = None
            if tl == "A":
                r = build(x, TL.PLANS["A_trio"], i)
                trio_ok = bool(r and r["gate"])
            sel = TL.sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent,
                                    trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            axis_ok = bool(_G.passes_axis_gate(plan.key, x.shape.axis_sum, 7))
            r = build(x, plan, i)
            if r is None:
                continue
            rows.append(dict(i=i, win=win, date=x.date, type=tl, rtype=x.rtype,
                             axis=float(x.shape.axis_sum), axis_ok=axis_ok, **r))
    print(f"行 {len(rows):,}")
    pickle.dump(rows, Path(args.out).open("wb"))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
