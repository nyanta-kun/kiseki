#!/usr/bin/env python3
"""現行商品（1レース1商品）を本番経路で組み、買い目・賭け金ごと焼き付ける（2026-09-12）。

`scripts/exp_type_lab/tau_stacked_build.py` と同じ組み方（sell_plans_for →
build_with_gate_fallback → passes_axis_gate）に、live と同じ `order_probs`
（`rank_7t3_order_swap_probs`）を渡す。UPPER_BANDS は現在空なので上帯は無い。

出力: <scratch>/07_mark_upset/product.pkl   1行 = 1レース（組めたもの）
  legs/stakes（買い目→賭け金）、plan、gate、axis_ok、決着、確定オッズ、
  決着の目の予測オッズ・確率順位 等。
"""
from __future__ import annotations

import importlib.util
import itertools
import pickle
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
import src.type_lab as TL  # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs, rank_7t3_order_swap_probs  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]

OUT = Path(__file__).resolve().parent / "product.pkl"
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()


class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date", "rtype", "order_probs")


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
    x.po_tf, x.pr_tf = po, pr
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
    x.order_probs = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    return x


def build(x: Ctx, plan) -> dict | None:
    trio = plan.bet_type == "trio"
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    got = TL.build_with_gate_fallback(
        x.shape, plan, pod, prb, 7, order_probs=None if trio else x.order_probs)
    if not got:
        return None
    legs, st, pl = got
    mean = float(TL.mean_expected_payout(st, pod))
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
        win_po = float(x.po_t3.get(x.win_t3, np.nan))
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
        win_po = float(x.po_tf.get(x.win_tf, np.nan))
    return dict(key=pl.key, trio=trio, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate), min_odds=float(pl.min_odds),
                stakes={c: int(v) for c, v in st.items()},
                po_legs={c: float(pod[c]) for c in st},
                win_po=win_po, sigma=float(sum(1.0 / pod[c] for c in st)))


def main() -> None:
    t0 = time.time()
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if str(Z["TYPE"][int(i)]) in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}  ({time.time()-t0:.0f}s)", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = x.shape.type_label
            trio_ok = None
            if tl == "A":
                r = build(x, TL.PLANS["A_trio"])
                trio_ok = bool(r and r["gate"])
            sel = TL.sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent, trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            axis_ok = bool(_G.passes_axis_gate(plan.key, x.shape.axis_sum, 7))
            r = build(x, plan)
            if r is None:
                continue
            # 決着の目の確率順位（三連単・λμ込み）
            order = sorted(x.pr_tf, key=lambda c: -x.pr_tf[c])
            rank_prob = order.index(x.win_tf) if x.win_tf in x.pr_tf else 999
            rows.append(dict(i=i, win=win, date=x.date, type=tl, rtype=x.rtype,
                             axis=float(x.shape.axis_sum), axis_ok=axis_ok,
                             pw_ent=float(x.shape.pw_ent),
                             a1=x.shape.order[0], a2=x.shape.order[1],
                             fin=x.win_tf, pay_tf=x.pay_tf, odds_t3=x.odds_t3,
                             rank_prob=rank_prob,
                             pr_win=float(x.pr_tf.get(x.win_tf, 0.0)),
                             **r))
    print(f"行 {len(rows):,}  ({time.time()-t0:.0f}s)")
    pickle.dump(rows, OUT.open("wb"))
    print("→", OUT)


if __name__ == "__main__":
    main()
