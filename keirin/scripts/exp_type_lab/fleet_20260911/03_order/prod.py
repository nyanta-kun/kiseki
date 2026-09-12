#!/usr/bin/env python3
"""学習した順序モデルを**買い目まで通す**（本番経路・2026-09-11）。

ベースライン ⓪ = main の現行（λ=2.0/μ=1.5 + λr=1.9 の `apply_order_swap`）。
腕:
  cur     現行（λr1.9）
  L1      学習した4定数（λ/μ/λr/μr を条件付きロジットで推定）
  L4      学習した順序モデル（ライン交互作用・隊列位置・脚質・印・pw/p3交互作用）
  L4all   L4 を三連単の当てにいく全プラン（A/B/C/E/F_hit）へ広げる

🔴 選択（`build_legs`）は一切触らない。`order_probs` だけを差し替える
   ＝件数・点数・組合せは動かない（`apply_order_swap` の設計どおり）。
🔴 モデルは vintage:
   確認窓 2026-01〜08 → fold B（train <= 2025-12-31）
   OOS窓 2025-07〜12  → fold A（train <= 2025-06-30）
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import math
import os
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                          # noqa: E402
from src.strategy_wt import (rank_7t3_blend_probs,                 # noqa: E402
                            rank_7t3_order_swap_probs)

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
sys.path.insert(0, str(HERE))
import common as C                                                 # noqa: E402
from build_ds import SETS                                          # noqa: E402
from feat210 import feat210                                        # noqa: E402
from plbase import base_pl                                         # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                          # type: ignore[union-attr]

MIN_MEAN, MIN_PO = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3
TRIF_HIT = frozenset({"A_hit", "B_hit", "C_hit", "E_hit", "F_hit"})
BETA = pickle.load(open("/tmp/oc/beta.pkl", "rb"))
NAMES_ALL = SETS["L5_mkt"]

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--out", default="/tmp/oc/prod.pkl")
A = ap.parse_args()

WINDOWS = [("confirm", "2026-01-01", "2026-12-31", "B"),
           ("oos25h2", "2025-07-01", "2025-12-31", "A")]


def beta_vec(tag: str, name: str) -> np.ndarray:
    """特徴名の全集合 `NAMES_ALL` に合わせた係数ベクトル（使わない列は 0）。"""
    d = BETA[(tag, name)]
    return np.array([d.get(c, 0.0) for c in NAMES_ALL])


def learned_probs(i: int, bv: np.ndarray, base: np.ndarray) -> dict:
    v = feat210(Z["LG"][i], Z["A_line_pos"][i], Z["A_line_size"][i],
                Z["A_is_line_leader"][i], Z["ST"][i], Z["A_prediction_mark"][i],
                _ZPW[i], _ZP3[i], _ZRP[i], Z["PO"][i].astype(np.float64))
    cols = [_FIDX[c] for c in NAMES_ALL]
    s = np.log(np.maximum(base, 1e-300)) + v[:, cols] @ bv
    s = s - s.max()
    e = np.exp(s)
    e[base <= 0] = 0.0
    e /= e.sum()
    return {PERMS[t]: float(e[t]) for t in range(210)}


def zsc(a: np.ndarray) -> np.ndarray:
    m = a.mean(1, keepdims=True)
    return (a - m) / np.maximum(a.std(1, keepdims=True), 1e-9)


from build_ds import IDX as _FIDX                                  # noqa: E402

_ZPW = zsc(Z["PW"].astype(np.float64))
_ZP3 = zsc(Z["P3"].astype(np.float64))
_ZRP = zsc(Z["A_race_point"].astype(np.float64))


def main() -> None:
    BASE = base_pl(Z["PW"], Z["P3"])
    rows: list[dict] = []
    for win, d0, d1, fold in WINDOWS:
        bv1 = beta_vec(fold, "L1_pair4")
        bv4 = beta_vec(fold, "L4_inter")
        bv5 = beta_vec(fold, "L5_mkt")
        idx = [int(i) for i in C.select(None, "all")
               if d0 <= str(Z["DATE"][int(i)]) <= d1 and str(Z["TYPE"][int(i)]) in "ABCDEF"]
        if A.limit:
            idx = idx[:A.limit]
        print(f"=== {win} n={len(idx):,} (fold {fold}) ===", flush=True)
        for n, i in enumerate(idx):
            if n % 2000 == 0:
                print(f"  {n:,}/{len(idx):,}", flush=True)
            cars = list(range(1, 8))
            p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
            pw = {c: float(Z["PW"][i][c - 1]) for c in cars}
            lg = {c: str(Z["LG"][i][c - 1]) for c in cars}
            lp = {c: (int(v) if np.isfinite(v) else None)
                  for c, v in ((c, Z["A_line_pos"][i][c - 1]) for c in cars)}
            po = {PERMS[t]: float(Z["PO"][i][t]) for t in range(210)
                  if np.isfinite(Z["PO"][i][t]) and Z["PO"][i][t] > 0}
            if len(po) < 60:
                continue
            pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
            orp = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
            po3 = {frozenset(c): float(Z["TRIO_PO"][i][j]) for j, c in enumerate(C3)
                   if np.isfinite(Z["TRIO_PO"][i][j]) and Z["TRIO_PO"][i][j] > 0}
            pr3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
                   for c in C3}
            order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
            lines = TL._lines_of(lg, lp)
            tl = str(Z["TYPE"][i])
            shape = TL.RaceShape(tl, float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
                                 float(Z["GAP"][i]),
                                 float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
                                 order, TL.win_entropy(pw), lines,
                                 TL._strongest_pair(lines, p3))
            trio_ok = None
            if tl == "A":
                g = TL.build_with_gate_fallback(shape, TL.PLANS["A_trio"], po3, pr3, 7)
                trio_ok = bool(g and float(TL.mean_expected_payout(g[1], po3)) > MIN_MEAN
                               and min(float(po3[c]) for c in g[1]) >= MIN_PO)
            sel = TL.sell_plans_for(tl, 7, str(Z["RTYPE"][i]),
                                    pw_ent=shape.pw_ent, trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            if not _G.passes_axis_gate(plan.key, shape.axis_sum, 7):
                continue
            trio = plan.bet_type == "trio"
            pod, prb = (po3, pr3) if trio else (po, pr)
            wtf, ptf = PERMS[int(Z["WIN"][i])], float(Z["PAY"][i]) / 100.0
            w3, o3 = frozenset(C3[int(Z["TRIO_WIN"][i])]), float(Z["TRIO_PAY"][i])

            lp1 = lp4 = lp5 = lmk = None
            if not trio:
                lp1 = learned_probs(i, bv1, BASE[i])
                lp4 = learned_probs(i, bv4, BASE[i])
                lp5 = learned_probs(i, bv5, BASE[i])
                inv_ = {p: 1.0 / max(v, 1e-9) for p, v in po.items()}
                s_ = sum(inv_.values())
                lmk = {p: v / s_ for p, v in inv_.items()}

            def run(oprobs, plans, sel_probs=None):
                old = TL.ORDER_SWAP_PLANS
                TL.ORDER_SWAP_PLANS = plans
                try:
                    got = TL.build_with_gate_fallback(
                        shape, plan, pod, sel_probs if sel_probs is not None else prb, 7,
                        order_probs=None if trio else oprobs)
                finally:
                    TL.ORDER_SWAP_PLANS = old
                if not got:
                    return None
                legs, st, pl = got
                mean = float(TL.mean_expected_payout(st, pod))
                if mean <= MIN_MEAN or min(float(pod[c]) for c in st) < MIN_PO:
                    return None
                if trio:
                    pay = float(st[w3] * o3) if w3 in st else 0.0
                else:
                    pay = float(st[wtf] / 100.0 * ptf * 100.0) if wtf in st else 0.0
                inv = float(sum(st.values()))
                sets = ({w3} if trio else {frozenset(c) for c in st})
                return dict(k=len(st), inv=inv, pay=pay, mean=mean,
                            key=pl.key, legs=[tuple(c) if not trio else c for c in st],
                            sethit=bool((w3 in st) if trio
                                        else frozenset(wtf) in sets))

            base_plans = frozenset({"B_hit", "F_hit"})
            r = {}
            r["cur"] = run(orp, base_plans)
            if r["cur"] is None:
                continue
            if trio or plan.key not in base_plans:
                r["L1"] = r["L4"] = r["L5"] = r["mkt"] = r["cur"]
            else:
                r["L1"] = run(lp1, base_plans) or r["cur"]
                r["L4"] = run(lp4, base_plans) or r["cur"]
                r["L5"] = run(lp5, base_plans) or r["cur"]
                r["mkt"] = run(lmk, base_plans) or r["cur"]
            # 🔴 選択（build_legs）まで学習確率にした腕（件数が動くので対照が要る）
            r["selL4"] = (run(lp4, base_plans, sel_probs=lp4) if not trio else r["cur"])
            r["selL1"] = (run(lp1, base_plans, sel_probs=lp1) if not trio else r["cur"])
            if trio or plan.key not in TRIF_HIT:
                r["L4all"] = r["cur"]
            elif plan.key in base_plans:
                r["L4all"] = r["L4"]
            else:
                r["L4all"] = run(lp4, TRIF_HIT) or r["cur"]
            # ── 並べ替えオラクル（正解の並びを知っていたら）と到達可能性 ──
            orc = dict(r["cur"])
            reach = dict(miss=False, inband=False, gate=False)
            if (not trio) and r["cur"]["sethit"] and r["cur"]["pay"] <= 0:
                reach["miss"] = True
                legs0 = [tuple(c) for c in r["cur"]["legs"]]
                lo = float(plan.min_odds or 0.0)
                hi = float(plan.max_odds or 0.0)
                o = po.get(wtf)
                inband = bool(o and o > 0 and float(o) >= max(lo, MIN_PO)
                              and (not hi or float(o) <= hi))
                reach["inband"] = inband
                if inband and wtf not in legs0:
                    j = next((t for t, c in enumerate(legs0)
                              if frozenset(c) == frozenset(wtf)), None)
                    if j is not None:
                        out = list(legs0)
                        out[j] = wtf
                        st2 = TL.allocate(out, po, orp, plan)
                        if (st2 and len(st2) == len(out)
                                and float(TL.mean_expected_payout(st2, po)) > MIN_MEAN
                                and min(float(po[c]) for c in st2) >= MIN_PO):
                            reach["gate"] = True
                            orc = dict(k=len(st2), inv=float(sum(st2.values())),
                                       pay=float(st2[wtf] / 100.0 * ptf * 100.0),
                                       mean=float(TL.mean_expected_payout(st2, po)),
                                       key=plan.key, legs=out, sethit=True)
            r["orc"] = orc
            rows.append(dict(i=i, win=win, date=str(Z["DATE"][i]), type=tl,
                             plan=plan.key, trio=trio, arms=r, reach=reach))
    print(f"行 {len(rows):,}")
    pickle.dump(rows, open(A.out, "wb"))
    print(f"→ {A.out}")


if __name__ == "__main__":
    main()
