#!/usr/bin/env python3
"""λr（並べ替え）を **τ適応の上に積んだ本番経路**で測るための台（2026-09-11）。

`tau_stacked_build.py` の拡張。違いは2つだけ:

- `build_with_gate_fallback` に **`order_probs`（λr 込みの三連単確率）を渡す**
  （三連複には渡さない＝順序が無い）。main 側にはこの引数が無いので自動で落とす。
- `apply_order_swap` を包んで **発動・入れ替え点数・帯違反・ゲートで見送り** を拾う。

腕は3つ:

    ⓪ 現行        `--repo <main>`
    ① τ適応のみ   `--repo <worktree> --no-order-swap`（`ORDER_SWAP_PLANS` を空に）
    ② τ適応+λr    `--repo <worktree>`

🔴 `RaceShape` には `lines` / `line_pair` を必ず渡す（`apply_line_swap` が発動する）。
   `A_line_pos` は float32 なので `int()` してから渡さないと
   `_lines_of` の `int(str(v))` が `ValueError` になり全車が最後尾扱いになる。

    PYTHONPATH=<repo> python order_swap_build.py --repo <repo> --out <pkl>
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
                help="`ORDER_SWAP_PLANS` を空にして λr を無効化する（腕①）")
ap.add_argument("--order-swap-plans", default=None,
                help="カンマ区切りでプランを指定（切り分け用）")
ap.add_argument("--no-line-swap", action="store_true",
                help="`LINE_SWAP_PLANS` を空にして `apply_line_swap` を無効化する"
                     "（2x2 の切り分け用）")
args = ap.parse_args()

REPO = Path(args.repo).resolve()
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs             # noqa: E402

HAS_ORDER = hasattr(TL, "apply_order_swap")
if HAS_ORDER:
    from src.strategy_wt import rank_7t3_order_swap_probs    # noqa: E402
    if args.no_order_swap:
        TL.ORDER_SWAP_PLANS = frozenset()
    elif args.order_swap_plans is not None:
        TL.ORDER_SWAP_PLANS = frozenset(
            k for k in args.order_swap_plans.split(",") if k)

if args.no_line_swap:
    TL.LINE_SWAP_PLANS = frozenset()

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                    # type: ignore[union-attr]

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0
PERMS = C.CANON
C3 = C.CANON3

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

# ── 差し替え / 並べ替えの発動を拾う包み ──
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
        cand = (plan.key in TL.ORDER_SWAP_PLANS and plan.bet_type == "trifecta"
                and bool(order_probs) and bool(legs))
        _ST["ocand"] = bool(cand)
        _ST["ochg"] = 0
        _ST["oviol"] = 0
        _ST["ogate"] = False
        _ST["oin"] = [tuple(c) for c in legs]
        _ST["ostakes"] = dict(stakes)
        _ST["opo"] = pred_odds
        _ST["oprob"] = order_probs
        _ST["oplan"] = plan
        out = _orig_order(plan, legs, stakes, pred_odds, order_probs, min_mean_payout)
        if not cand:
            return out
        a = [tuple(c) for c in legs]
        b = [tuple(c) for c in out[0]]
        _ST["ochg"] = sum(1 for x, y in zip(a, b) if x != y)
        lo = float(plan.min_odds or 0.0)
        hi = float(plan.max_odds or 0.0)
        v = 0
        for x, y in zip(a, b):
            if x == y:
                continue
            o = float(pred_odds.get(y, 0.0) or 0.0)
            if o < lo or (hi and o > hi):
                v += 1
        _ST["oviol"] = v
        # 🔴 入稿ゲートで見送った分を数える（ゲートを外して同じ計算をする）
        if _ST["ochg"] == 0:
            ung = _orig_order(plan, legs, stakes, pred_odds, order_probs, -1e18)
            _ST["ogate"] = [tuple(c) for c in ung[0]] != a
        return out

    TL.apply_order_swap = _w_order


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


def build(x: Ctx, plan) -> dict | None:
    trio = plan.bet_type == "trio"
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    _ST.clear()
    _ST.update(line_hit=False, ocand=False, ochg=0, oviol=0, ogate=False)
    if HAS_ORDER:
        got = TL.build_with_gate_fallback(
            x.shape, plan, pod, prb, 7,
            order_probs=None if trio else x.or_tf)
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
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
        sets = {frozenset(c) for c in st}
        sethit = frozenset(x.win_tf) in sets
        exact = x.win_tf in st
    return dict(key=pl.key, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate), swapped=bool(_ST.get("line_hit")),
                ocand=bool(_ST.get("ocand")), ochg=int(_ST.get("ochg") or 0),
                oviol=int(_ST.get("oviol") or 0),
                ogate=bool(_ST.get("ogate")),
                sethit=bool(sethit), exact=bool(exact),
                min_odds=float(pl.min_odds))


def main() -> None:
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if str(Z["TYPE"][int(i)]) in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = x.shape.type_label
            trio_ok = None
            if tl == "A":
                r = build(x, TL.PLANS["A_trio"])
                trio_ok = bool(r and r["gate"])
            sel = TL.sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent,
                                    trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            axis_ok = bool(_G.passes_axis_gate(plan.key, x.shape.axis_sum, 7))
            r = build(x, plan)
            if r is None:
                continue
            rows.append(dict(i=i, win=win, date=x.date, type=tl, rtype=x.rtype,
                             axis=float(x.shape.axis_sum), axis_ok=axis_ok,
                             n_lines=len(x.shape.lines), **r))
    print(f"行 {len(rows):,}")
    pickle.dump(rows, Path(args.out).open("wb"))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
