#!/usr/bin/env python3
"""帯 L × 点数 k を**レースごとに条件付きで同時に動かす**設計の台（2026-09-10）。

## 先に本番を読む（CLAUDE.md「測る前に本番コードを読む」）

- `PLANS["C_hit"]` = prob_top / min_odds=15.0 / max_legs=12 / alloc='conf' /
  floor_mult=MIN_PAYOUT_MULT / **underband_min=5.0**（帯の下から最人気1点を差し込み、
  確率最下位の1点と入れ替える＝点数は12点のまま）。
- `PLANS["E_hit"]` = prob_top / min_odds=30.0 / max_legs=14 / alloc='conf' /
  floor_mult=MIN_PAYOUT_MULT / underband なし。
- `GATE_FALLBACK["C_hit"]` = 差込なしの L=15 k=12。**ゲートに落ちたら在庫を消さずに戻す。**
- 入稿ゲート: 平均想定払戻 > 20,000円 かつ 全点の予測オッズ >= 2.0倍。
- 軸信頼ゲート: `E_hit` 1.245 / **`C_hit` は exempt**（窓で符号反転のため意図的に外れている）。

## この台が作るもの

型C・型E の全レースについて、(L, k) の格子それぞれで
「組めたか・ゲートを通ったか・投資・払戻・平均想定払戻・点数」を焼き付ける。
条件量（本線オッズ・Σp5・τ・axis_sum・gap・arare・確率上位k点の予測オッズ中央値）も同じ行に置く。
他の型は現行の売り商品を1つだけ組む（ラインナップ集計用）。
"""
from __future__ import annotations

import importlib.util
import itertools
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, build_legs, build_with_gate_fallback,
    mean_expected_payout)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/band_by_race_rows.pkl")

#: 掃引する格子。現行は C=(15,12) / E=(30,14)。
GRID = {
    "C": ([0.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0],
          [3, 4, 5, 6, 8, 10, 12, 14, 16]),
    "E": ([0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0],
          [3, 4, 5, 6, 8, 10, 12, 14, 18]),
}
#: 帯下の1点差込。**型C だけ**（本番と同じ）。L<=5 や L=0 では `_insert_underband` が
#: 自動的に何もしないので、二重計上にはならない。
UB = {"C": 5.0, "E": 0.0}


def arm_plans(tl: str):
    """(L, k) -> Plan（本番の `PLANS[f"{tl}_hit"]` を replace で作る＝二重管理にしない）。"""
    base = PLANS[f"{tl}_hit"]
    Ls, ks = GRID[tl]
    out = {}
    for L in Ls:
        for k in ks:
            out[(L, k)] = replace(base, min_odds=L, max_legs=k,
                                  underband_min=UB[tl] if L > UB[tl] else 0.0)
    return out


def run_plan(x, plan) -> dict | None:
    """1レース1腕。組めなければ None。ゲート結果は `gate` に持つ（落としてから返さない）。"""
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    gate = (mean > MIN_MEAN_PAYOUT
            and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS)
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=float(mean), gate=bool(gate))


def prod_row(x, key: str) -> dict | None:
    """本番の買い方（`build_with_gate_fallback` を通す＝`GATE_FALLBACK` 込み）。"""
    got = build_with_gate_fallback(x.shape, PLANS[key], x.po_tf if PLANS[key].bet_type
                                   != "trio" else x.po_t3,
                                   x.pr_tf if PLANS[key].bet_type != "trio" else x.pr_t3,
                                   n_entries=7)
    if not got:
        return None
    legs, st, pl = got
    trio = pl.bet_type == "trio"
    pod = x.po_t3 if trio else x.po_tf
    mean = mean_expected_payout(st, pod)
    gate = (mean > MIN_MEAN_PAYOUT
            and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS)
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(k=len(st), inv=float(sum(st.values())), pay=pay, mean=float(mean),
                gate=bool(gate), L=float(pl.min_odds), ub=float(pl.underband_min))


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    MK = z["A_prediction_mark"]
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    APL = {t: arm_plans(t) for t in ("C", "E")}
    keys = {t: list(APL[t]) for t in ("C", "E")}
    rows, arms = [], {"C": [], "E": []}

    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 3000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = tp[i]
            trio_ok = (run_plan(x, PLANS["A_trio"]) or {}).get("gate", False) if tl == "A" else False
            key = _plan_for(tl, rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            base = prod_row(x, key)
            if base is None:
                continue

            # ── 条件量（すべて朝に分かる）─────────────────────────────
            # 本線 = 三連複 {◎○△} の予測オッズ（`mark_order_2026_09_10.md` 追補）
            mk = {c: str(MK[i][c - 1]) for c in range(1, 8)}
            m3 = [c for c in range(1, 8) if mk[c] in ("◎", "○", "▲")]
            hs_trio = q_m3 = float("nan")
            if len(m3) == 3:
                fs = frozenset(m3)
                hs_trio = float(x.po_t3.get(fs, np.nan))
                q_m3 = float(x.pr_t3.get(fs, np.nan))
            # 確率降順の三連単（帯なし）
            order = sorted(x.pr_tf, key=lambda c: -x.pr_tf[c])
            cum = np.cumsum([x.pr_tf[c] for c in order])
            po_sorted = [float(x.po_tf[c]) for c in order]
            def med_po(k):
                return float(np.median(po_sorted[:k]))
            def sig(k):
                return float(sum(1.0 / v for v in po_sorted[:k]))
            p3rank = {c: j for j, c in enumerate(x.shape.order)}
            wt = x.win_tf
            fin_ranks = tuple(p3rank[c] for c in wt)
            rows.append(dict(
                i=i, win=win, date=x.date, type=tl, plan=key, rtype=rt[i],
                axis=float(z["AXIS_SUM"][i]), gap=float(z["GAP"][i]),
                arare=int(z["ARARE"][i]), pw_ent=float(x.shape.pw_ent),
                hs_trio=hs_trio, q_m3=q_m3,
                sp3=float(cum[2]), sp5=float(cum[4]), sp8=float(cum[7]),
                sp12=float(cum[11]), sp14=float(cum[13]),
                med_po5=med_po(5), med_po12=med_po(12), med_po14=med_po(14),
                sig12=sig(12), sig14=sig(14),
                po_win=float(x.po_tf.get(wt, np.nan)), pay_tf=float(x.pay_tf),
                fin_ranks=fin_ranks,
                jundo=bool(set(fin_ranks) <= {0, 1, 2, 3} and {0, 1} <= set(fin_ranks)),
                base=base, arm_i=len(arms[tl]) if tl in ("C", "E") else -1,
            ))
            if tl in ("C", "E"):
                a = np.full((len(keys[tl]), 5), np.nan, dtype=np.float32)
                for j, kk in enumerate(keys[tl]):
                    r = run_plan(x, APL[tl][kk])
                    if r:
                        a[j] = (r["k"], r["inv"], r["pay"], r["mean"], r["gate"])
                arms[tl].append(a)

    print(f"作った行 {len(rows):,}  型C腕 {len(arms['C']):,}  型E腕 {len(arms['E']):,}")
    pickle.dump(dict(rows=rows, keys=keys,
                     arms={t: np.stack(arms[t]) for t in ("C", "E")}),
                OUT.open("wb"))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
