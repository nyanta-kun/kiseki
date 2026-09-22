#!/usr/bin/env python3
"""ラインナップ全体の台（2026-09-22）。

本番の `src/type_lab.py` をそのまま呼び、/tmp/race_type_board.npz（vintage 7車板）で
「1日ぶんの商品」を組み直す。日次上限・軸信頼ゲート・高額枠まで再現する。

🔴 測る前に本番を読む: 売る買い方は `sell_plans_for`、組むのは
   `build_with_gate_fallback` → `allocate`、ゲートは backend の
   `keirin_type_lab_gate`。ここには規則を書かない。
"""
from __future__ import annotations
import importlib.util, itertools, sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.type_lab import (                                    # noqa: E402
    BUDGET, PLANS, add_upper_band, allocate, build_with_gate_fallback,
    highpay_plan_for, mean_expected_payout, race_shape, sell_plans_for,
    HIGHPAY_SLOTS_PER_DAY, HIGHPAY_PLAN_KEYS)
from src.strategy_wt import rank_7t3_blend_probs, rank_7t3_order_swap_probs  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s); _s.loader.exec_module(_G)

PERMS = list(itertools.permutations(range(1, 8), 3))
C3 = list(itertools.combinations(range(1, 8), 3))
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0
PAYBACK = 0.75

_Z = None
#: 🔴 **NpzFile の添字アクセスは毎回配列を丸ごと展開する。** 一度だけ dict へ materialize する
#:    （これを怠ると1レース 2秒かかる）。
_NEED = ("PO", "WIN", "PAY", "KEY", "DATE", "P3", "PW", "LG", "ST", "A_line_pos",
         "A_race_point", "BEHIND", "DAYI", "TRIO_PO", "TRIO_WIN", "TRIO_PAY",
         "TYPE", "AGREE", "AXIS_SUM", "GAP", "RTYPE", "CUPG", "OKPRED", "GRADE")


def board():
    global _Z
    if _Z is None:
        z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
        _Z = {k: z[k] for k in _NEED}
    return _Z


def _fold_to_trio(tf_odds, tf_prob):
    """三連単板 → 三連複板（本番 build_type_lab_picks._fold_to_trio と同じ式）。"""
    o, p = {}, {}
    for c in C3:
        k = frozenset(c)
        s = sum(1.0 / tf_odds[q] for q in itertools.permutations(c) if q in tf_odds)
        if s > 0:
            o[k] = 1.0 / s
        p[k] = sum(tf_prob.get(q, 0.0) for q in itertools.permutations(c))
    return o, p


class Ctx:
    """pickle できるよう素の属性で持つ（台のキャッシュに載せる）。"""


def ctx(i: int):
    z = board()
    cars = list(range(1, 8))
    p3 = {c: float(z["P3"][i][c-1]) for c in cars}
    pw = {c: float(z["PW"][i][c-1]) for c in cars}
    lg = {c: str(z["LG"][i][c-1]) for c in cars}
    lp = {c: float(z["A_line_pos"][i][c-1]) for c in cars}
    st = {c: str(z["ST"][i][c-1]) for c in cars}
    rp = {c: float(z["A_race_point"][i][c-1]) for c in cars}
    bh = {c: float(z["BEHIND"][i][c-1]) for c in cars}
    shape = race_shape(p3, lg, lp, st, rp, bh, int(z["DAYI"][i]), pw)
    if shape is None:
        return None
    po = {PERMS[t]: float(z["PO"][i][t]) for t in range(210)
          if np.isfinite(z["PO"][i][t]) and z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    x = Ctx()
    x.shape = shape
    x.po_tf = po
    x.pr_tf = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    x.ord_tf = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    x.po_t3, x.pr_t3 = _fold_to_trio(po, x.pr_tf)
    x.win_tf = PERMS[int(z["WIN"][i])]
    x.pay_tf = float(z["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(z["TRIO_WIN"][i])])
    x.odds_t3 = float(z["TRIO_PAY"][i])
    x.date = str(z["DATE"][i]); x.rtype = str(z["RTYPE"][i]); x.cupg = str(z["CUPG"][i])
    x.key = str(z["KEY"][i])
    vals = [v for v in rp.values() if v and v > 0]
    x.rp_sd = float(np.std(vals)) if len(vals) >= 2 else None
    return x


def build(x: Ctx, plan, plans_override=None):
    """(stakes, odds, plan_used, mean) or None。plans_override で PLANS を差し替える。"""
    odds, prob = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    got = build_with_gate_fallback(
        x.shape, plan, odds, prob, 7,
        order_probs=None if plan.bet_type == "trio" else x.ord_tf)
    if not got:
        return None
    legs, stakes, used = got
    mean = mean_expected_payout(stakes, odds)
    legs, stakes, _roles = add_upper_band(legs, stakes, used, odds, prob, 7)
    return stakes, odds, used, mean


def gate_ok(stakes, odds, mean):
    return mean > MIN_MEAN_PAYOUT and min(float(odds[c]) for c in stakes) >= MIN_POINT_ODDS


def settle(x: Ctx, stakes, trio: bool):
    inv = float(sum(stakes.values()))
    if trio:
        pay = float(stakes[x.win_t3] * x.odds_t3) if x.win_t3 in stakes else 0.0
    else:
        pay = float(stakes[x.win_tf] * x.pay_tf) if x.win_tf in stakes else 0.0  # PAY/100 = 確定オッズ
    return inv, pay
