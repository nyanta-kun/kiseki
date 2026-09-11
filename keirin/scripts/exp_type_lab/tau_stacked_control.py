#!/usr/bin/env python3
"""τ適応の**無作為対照**（`apply_line_swap` 込みの台・2026-09-11）。

τ適応が選んだ点数の**多重集合はそのままに、どのレースへ割り当てるかだけを壊す**
20 seed。件数を変えずに中身を入れ替える操作なので、CLAUDE.md の作法どおり
「同数の無作為対照に勝つか」を見る（`race_filter_2026_08_27.md` と同型）。

ゲートに落ちた seed のレースは**現行（12点）へ戻す**（τ適応側と同じ扱い）。
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs             # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                    # type: ignore[union-attr]

MIN_MEAN, MIN_PO = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3
_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()
BASE = TL.PLANS["C_hit"]
KS = list(range(TL.TAU_ADAPTIVE_MIN_LEGS, TL.TAU_ADAPTIVE_MAX_LEGS + 1))

#: 🔴🔴 **`GATE_FALLBACK["C_hit"]` は `replace(PLANS["C_hit"], underband_min=0.0)` で
#:    作られているので `tau_adaptive=True` を継承する。** 主プランの τ適応だけ切っても
#:    ゲートに落ちたレースは代替側で τ適応が走り、固定k の腕が汚染される
#:    （最初の版で実際に踏み、現行腕が 33.58 → 33.82% に上振れした）。
#:    固定k を組むときは**代替も含めて切る**こと。
FB_TAU = TL.GATE_FALLBACK["C_hit"]
FB_OFF = tuple(replace(p, tau_adaptive=False) for p in FB_TAU)


def prep(i: int):
    cars = list(range(1, 8))
    p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
    pw = {c: float(Z["PW"][i][c - 1]) for c in cars}
    lg = {c: str(Z["LG"][i][c - 1]) for c in cars}
    lp = {c: (int(v) if np.isfinite(v) else None)
          for c, v in ((c, Z["A_line_pos"][i][c - 1]) for c in cars)}
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    po = {PERMS[t]: float(Z["PO"][i][t]) for t in range(210)
          if np.isfinite(Z["PO"][i][t]) and Z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    lines = TL._lines_of(lg, lp)
    shape = TL.RaceShape(str(Z["TYPE"][i]), float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
                         float(Z["GAP"][i]), float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
                         order, TL.win_entropy(pw), lines, TL._strongest_pair(lines, p3))
    return shape, po, pr


def run(shape, po, pr, k, win_tf, pay_tf):
    # 🔴 `tau_adaptive=False` を忘れると **どの k でも τ適応の結果が返り**、
    #    対照が全 seed 同値になる（最初の版で実際に踏んだ）。
    TL.GATE_FALLBACK["C_hit"] = FB_OFF
    try:
        got = TL.build_with_gate_fallback(
            shape, replace(BASE, max_legs=k, tau_adaptive=False), po, pr, 7)
    finally:
        TL.GATE_FALLBACK["C_hit"] = FB_TAU
    if not got:
        return None
    legs, st, _ = got
    if TL.mean_expected_payout(st, po) <= MIN_MEAN:
        return None
    if min(float(po[c]) for c in st) < MIN_PO:
        return None
    pay = float(st[win_tf] / 100.0 * pay_tf * 100.0) if win_tf in st else 0.0
    return (len(st), float(sum(st.values())), pay)


def main() -> None:
    for win, label in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        idx = [int(i) for i in C.select("C", win)]
        cells, taus, cur = [], [], []
        for i in idx:
            g = prep(i)
            if g is None:
                continue
            shape, po, pr = g
            if TL.sell_plans_for("C", 7, str(Z["RTYPE"][i]))[0].key != "C_hit":
                continue
            if not _G.passes_axis_gate("C_hit", shape.axis_sum, 7):
                continue
            wtf, ptf = PERMS[int(Z["WIN"][i])], float(Z["PAY"][i]) / 100.0
            base = run(shape, po, pr, BASE.max_legs, wtf, ptf)
            if base is None:
                continue        # 現行がゲートに落ちるレースは母集団外
            # τ適応（本番実装）
            got = TL.build_with_gate_fallback(shape, BASE, po, pr, 7)
            legs, st, _ = got
            tpay = float(st[wtf] / 100.0 * ptf * 100.0) if wtf in st else 0.0
            taus.append((len(st), float(sum(st.values())), tpay))
            cur.append(base)
            cells.append([run(shape, po, pr, k, wtf, ptf) for k in KS])
        n = len(cur)
        kmul = np.array([t[0] for t in taus])
        shown = lambda rs: sum(1 for r in rs if r[2] > r[1]) / len(rs) * 100
        print(f"\n=== {label}  型C n={n:,} ===")
        print(f"  現行(12点)   表示的中 {shown(cur):.2f}%")
        print(f"  τ適応        表示的中 {shown(taus):.2f}%  平均点数 {kmul.mean():.2f}")
        rng = np.random.default_rng(20260911)
        wins, vals = 0, []
        for s in range(20):
            perm = rng.permutation(kmul)
            rs = []
            for j in range(n):
                k = int(perm[j])
                c = cells[j][KS.index(k)] if k in KS else None
                rs.append(c if c is not None else cur[j])
            v = shown(rs)
            vals.append(v)
            wins += shown(taus) > v
        print(f"  無作為対照20seed 中央 {np.median(vals):.2f}% "
              f"（範囲 {min(vals):.2f}〜{max(vals):.2f}）  τ適応の勝ち {wins}/20")


if __name__ == "__main__":
    main()
