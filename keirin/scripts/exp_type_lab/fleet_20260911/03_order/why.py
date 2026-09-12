#!/usr/bin/env python3
"""なぜ順序モデルの改善が商品に出ないのか —— 直せた 887 件の中身を見る。

並べ替えは「その1点の集合の、まだ買っていない・帯の中の並び」しか候補にできない。
候補が何本あって、各モデルが正解をその中の何位に置いたかを数える。
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import pickle
import sys
from collections import Counter
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
from build_ds import IDX as FIDX, SETS                             # noqa: E402
from feat210 import feat210                                        # noqa: E402
from plbase import base_pl                                         # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                          # type: ignore[union-attr]

MIN_MEAN, MIN_PO = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3
HIT = frozenset({"A_hit", "B_hit", "C_hit", "E_hit", "F_hit"})
BETA = pickle.load(open("/tmp/oc/beta.pkl", "rb"))
NAMES_ALL = SETS["L5_mkt"]
COLS = [FIDX[c] for c in NAMES_ALL]

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()


def zsc(a):
    return (a - a.mean(1, keepdims=True)) / np.maximum(a.std(1, keepdims=True), 1e-9)


ZPW, ZP3, ZRP = (zsc(Z["PW"].astype(np.float64)), zsc(Z["P3"].astype(np.float64)),
                 zsc(Z["A_race_point"].astype(np.float64)))
BASE = base_pl(Z["PW"], Z["P3"])
WINDOWS = [("confirm", "2026-01-01", "2026-12-31", "B"),
           ("oos25h2", "2025-07-01", "2025-12-31", "A")]


def main() -> None:
    for win, d0, d1, fold in WINDOWS:
        bv = np.array([BETA[(fold, "L4_inter")].get(c, 0.0) for c in NAMES_ALL])
        bv1 = np.array([BETA[(fold, "L1_pair4")].get(c, 0.0) for c in NAMES_ALL])
        idx = [int(i) for i in C.select(None, "all")
               if d0 <= str(Z["DATE"][int(i)]) <= d1 and str(Z["TYPE"][int(i)]) in "ABCDEF"]
        ncand = Counter()
        rank_cur, rank_l4, rank_mkt, rank_l1 = Counter(), Counter(), Counter(), Counter()
        nfix = 0
        for i in idx:
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
            if not sel or sel[0].bet_type != "trifecta" or sel[0].key not in HIT:
                continue
            plan = sel[0]
            if not _G.passes_axis_gate(plan.key, shape.axis_sum, 7):
                continue
            got = TL.build_with_gate_fallback(shape, plan, po, pr, 7, order_probs=orp)
            if not got:
                continue
            legs, st, pl = got
            if (float(TL.mean_expected_payout(st, po)) <= MIN_MEAN
                    or min(float(po[c]) for c in st) < MIN_PO):
                continue
            legs0 = [tuple(c) for c in st]
            wtf = PERMS[int(Z["WIN"][i])]
            if wtf in legs0 or frozenset(wtf) not in {frozenset(c) for c in legs0}:
                continue
            lo, hi = float(pl.min_odds or 0.0), float(pl.max_odds or 0.0)
            o = po.get(wtf)
            if not (o and float(o) >= max(lo, MIN_PO) and (not hi or float(o) <= hi)):
                continue
            j = next(t for t, c in enumerate(legs0) if frozenset(c) == frozenset(wtf))
            out = list(legs0)
            out[j] = wtf
            st2 = TL.allocate(out, po, orp, pl)
            if not (st2 and len(st2) == len(out)
                    and float(TL.mean_expected_payout(st2, po)) > MIN_MEAN
                    and min(float(po[c]) for c in st2) >= MIN_PO):
                continue
            nfix += 1
            # 候補（実装と同じ制約）
            have = set(legs0)
            cand = [legs0[j]]
            for q in itertools.permutations(sorted(set(legs0[j]))):
                if q == legs0[j] or q in have:
                    continue
                oq = po.get(q)
                if not oq or float(oq) < max(lo, MIN_PO) or (hi and float(oq) > hi):
                    continue
                cand.append(q)
            ncand[len(cand)] += 1
            v = feat210(Z["LG"][i], Z["A_line_pos"][i], Z["A_line_size"][i],
                        Z["A_is_line_leader"][i], Z["ST"][i],
                        Z["A_prediction_mark"][i], ZPW[i], ZP3[i], ZRP[i],
                        Z["PO"][i].astype(np.float64))
            lb = np.log(np.maximum(BASE[i], 1e-300))
            s4 = {p: lb[t] + float(v[t, COLS] @ bv) for t, p in enumerate(PERMS)}
            s1 = {p: lb[t] + float(v[t, COLS] @ bv1) for t, p in enumerate(PERMS)}
            for nm, sc, ctr in (("cur", lambda p: orp.get(p, 0.0), rank_cur),
                                ("l4", lambda p: s4[p], rank_l4),
                                ("l1", lambda p: s1[p], rank_l1),
                                ("mkt", lambda p: -po.get(p, 9e9), rank_mkt)):
                r = 1 + sum(1 for q in cand if q != wtf and sc(q) > sc(wtf))
                ctr[r] += 1
        print(f"\n=== {win}  並べ替えで直せた（＝正解が帯の中・ゲートも通る）{nfix:,} 件 ===")
        tot = sum(ncand.values())
        print("  候補数の分布（実装が選べる並びの本数・元の1点を含む）:",
              dict(sorted(ncand.items())))
        for nm, ctr in (("現行 λr", rank_cur), ("L1 学習4定数", rank_l1),
                        ("L4 学習モデル", rank_l4), ("市場", rank_mkt)):
            r1 = ctr[1] / max(tot, 1) * 100
            print(f"  {nm:<14} 正解を1位に置いた {ctr[1]:5,} / {tot:,} = {r1:5.1f}%"
                  f"   順位分布 {dict(sorted(ctr.items()))}")


if __name__ == "__main__":
    main()
