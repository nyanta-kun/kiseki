#!/usr/bin/env python3
"""掃引した各水準の **無作為対照 20 seed**（`apply_line_swap` 込み・2026-09-11）。

τ適応 が選んだ点数の**多重集合はそのままに、割り当てを壊す**。件数を変えない
振り分け操作なので必ず置く（CLAUDE.md・`race_filter_2026_08_27.md` と同型）。

固定k の買い目は**一度だけ**組んで使い回す（水準ごとに組み直すと時間が桁で増える）。

🔴 **`GATE_FALLBACK["C_hit"]` は `replace(PLANS["C_hit"], ...)` 製で `tau_adaptive=True` を
   継承する。** 固定k を組むときは代替側も切らないと腕が汚染される。

    python tau_stacked_control2.py 現行.pkl 12:m12.pkl 13:m13.pkl ...
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import pickle
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

MIN_MEAN, MIN_PO = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3
_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()
BASE = TL.PLANS["C_hit"]
FB_TAU = TL.GATE_FALLBACK["C_hit"]
FB_OFF = tuple(replace(p, tau_adaptive=False) for p in FB_TAU)

ARGS = sys.argv[1:]
BASE_PKL, ARMS = ARGS[0], [a.split(":", 1) for a in ARGS[1:]]


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


def fixed(shape, po, pr, k, wtf, ptf):
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
    pay = float(st[wtf] / 100.0 * ptf * 100.0) if wtf in st else 0.0
    return (pay > float(sum(st.values())))


def main() -> None:
    base_rows = pickle.load(open(BASE_PKL, "rb"))
    arms = [(lab, pickle.load(open(p, "rb"))) for lab, p in ARMS]
    for win, label in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        sb = {r["i"]: r for r in base_rows
              if r["win"] == win and r["gate"] and r["axis_ok"] and r["key"] == "C_hit"}
        sa = [(lab, {r["i"]: r for r in rows if r["win"] == win and r["gate"]
                     and r["axis_ok"] and r["key"] == "C_hit"}) for lab, rows in arms]
        ids = sorted(sb)
        need = sorted({st[i]["k"] for _, st in sa for i in ids if i in st})
        print(f"\n=== {label}  型C n={len(ids):,}  固定k を組む範囲 {need[0]}〜{need[-1]} ===",
              flush=True)
        cells: dict[int, dict[int, bool]] = {}
        for n, i in enumerate(ids):
            if n % 1000 == 0:
                print(f"  ... {n:,}/{len(ids):,}", flush=True)
            g = prep(i)
            if g is None:
                continue
            shape, po, pr = g
            wtf, ptf = PERMS[int(Z["WIN"][i])], float(Z["PAY"][i]) / 100.0
            cells[i] = {k: fixed(shape, po, pr, k, wtf, ptf) for k in need}
        cur = {i: (sb[i]["pay"] > sb[i]["inv"]) for i in ids}
        print(f"  現行(12点) 表示的中 {np.mean([cur[i] for i in ids])*100:.2f}%")
        rng = np.random.default_rng(20260911)
        for lab, st in sa:
            use = [i for i in ids if i in st and i in cells]
            kk = np.array([st[i]["k"] for i in use])
            tau = np.mean([st[i]["pay"] > st[i]["inv"] for i in use]) * 100
            vals = []
            for _ in range(20):
                perm = rng.permutation(kk)
                v = [cells[i].get(int(perm[j]))
                     for j, i in enumerate(use)]
                v = [x if x is not None else cur[i] for x, i in zip(v, use)]
                vals.append(np.mean(v) * 100)
            wins = sum(1 for v in vals if tau > v)
            print(f"  上限{lab:>4s}  τ適応 {tau:6.2f}%  "
                  f"対照20seed 中央 {np.median(vals):6.2f}%（{min(vals):.2f}〜{max(vals):.2f}）"
                  f"  勝ち {wins}/20", flush=True)


if __name__ == "__main__":
    main()
