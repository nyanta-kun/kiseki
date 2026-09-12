#!/usr/bin/env python3
"""型が入れ替わったレースだけ、現行/較正後それぞれの商品で実際に売って比べる。

型判定(RaceShape.axis_sum/firm/label)だけ較正後の値に差し替える。
価格（pr_tf/pr_t3・確率）は raw のまま（DESIGN.md 2.4「読み」と「価格」は別）。
軸2車の「識別」は raw の順位を使う（p3_calibration.py と同じ思想:
較正は値だけ動かし、順位選定は較正前のままでよい）。
"""
from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs              # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                            # noqa: E402

PERMS, C3 = C.CANON, C.CANON3

Z_NPZ = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: Z_NPZ[k] for k in Z_NPZ.files}
Z_NPZ.close()
N = len(Z["KEY"])


def odds_band_idx(pw: float) -> int:
    odds = 0.75 / pw if pw > 1e-9 else np.inf
    edges = [5, 10, 20, 30, 50, 100, 300, np.inf]
    for b, e in enumerate(edges):
        if odds <= e:
            return b
    return 7


def fit_deltas(explore_idx):
    """explore窓の軸2車から (concentration quintile, odds帯) セル別 delta を作る。"""
    P3, PW, AXIS_SUM, WIN = Z["P3"], Z["PW"], Z["AXIS_SUM"], Z["WIN"]
    conc_edges = np.quantile(AXIS_SUM[explore_idx], [0, .2, .4, .6, .8, 1.0])
    conc_edges[0], conc_edges[-1] = -np.inf, np.inf

    p3_all, top3_all, conc_all, band_all = [], [], [], []
    for i in explore_idx:
        p3 = P3[i]
        if not np.isfinite(p3).all():
            continue
        order = np.argsort(-p3)
        top3cars = set(PERMS[int(WIN[i])])
        cq = int(np.searchsorted(conc_edges, AXIS_SUM[i], side="right") - 1)
        for r in (0, 1):
            car = int(order[r]) + 1
            p3_all.append(float(p3[order[r]]))
            top3_all.append(1.0 if car in top3cars else 0.0)
            conc_all.append(cq)
            band_all.append(odds_band_idx(float(PW[i][order[r]])))
    p3_all, top3_all = np.array(p3_all), np.array(top3_all)
    conc_all, band_all = np.array(conc_all), np.array(band_all)

    cell_delta, conc_delta_1d, band_delta_1d = {}, {}, {}
    for cq in range(5):
        m = conc_all == cq
        conc_delta_1d[cq] = float(top3_all[m].mean() - p3_all[m].mean()) if m.sum() else 0.0
    for bd in range(8):
        m = band_all == bd
        band_delta_1d[bd] = float(top3_all[m].mean() - p3_all[m].mean()) if m.sum() else 0.0
    for cq in range(5):
        for bd in range(8):
            m = (conc_all == cq) & (band_all == bd)
            if m.sum() >= 30:
                cell_delta[(cq, bd)] = float(top3_all[m].mean() - p3_all[m].mean())
    return conc_edges, cell_delta, conc_delta_1d, band_delta_1d


def calibrated_top3_dict(i, conc_edges, cell_delta, conc_delta_1d, band_delta_1d):
    p3 = Z["P3"][i]
    pw = Z["PW"][i]
    cq = int(np.searchsorted(conc_edges, Z["AXIS_SUM"][i], side="right") - 1)
    order = np.argsort(-p3)
    out = {int(c) + 1: float(p3[c]) for c in range(7)}
    for r in (0, 1):
        c = int(order[r])
        bd = odds_band_idx(float(pw[c]))
        key = (cq, bd)
        d = cell_delta.get(key)
        if d is None:
            d = 0.5 * (conc_delta_1d.get(cq, 0.0) + band_delta_1d.get(bd, 0.0))
        out[c + 1] = float(np.clip(p3[c] + d, 1e-6, 1 - 1e-6))
    return out


class Ctx:
    __slots__ = ("shape_raw", "shape_cal", "po_tf", "pr_tf", "po_t3", "pr_t3",
                 "win_tf", "pay_tf", "win_t3", "odds_t3", "date", "rtype")


def build_ctx(i, conc_edges, cell_delta, conc_delta_1d, band_delta_1d, thr_cal):
    cars = list(range(1, 8))
    p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
    if not all(np.isfinite(v) for v in p3.values()):
        return None
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
    # raw shape（板の値そのもの）
    x.shape_raw = TL.RaceShape(
        str(Z["TYPE"][i]), float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
        float(Z["GAP"][i]), float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
        order, TL.win_entropy(pw), lines, TL._strongest_pair(lines, p3))

    # calibrated shape（axis_sum だけ差し替え。順位(order)・arare は不変）
    p3_cal = calibrated_top3_dict(i, conc_edges, cell_delta, conc_delta_1d, band_delta_1d)
    axis_sum_cal = p3_cal[order[0]] + p3_cal[order[1]]
    firm_cal = axis_sum_cal >= thr_cal
    s = int(Z["ARARE"][i])
    label_cal = ("A" if s <= -1 else "B" if s == 0 else "C") if firm_cal else \
                ("D" if s <= -1 else "E" if s == 0 else "F")
    x.shape_cal = TL.RaceShape(label_cal, axis_sum_cal, s, float(Z["GAP"][i]), firm_cal,
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
    return x


MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0


def run_plan(x: Ctx, shape, plan) -> dict | None:
    trio = plan.bet_type == "trio"
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    got = TL.build_with_gate_fallback(shape, plan, pod, prb, 7)
    if not got:
        return None
    legs, st, pl = got
    mean = float(TL.mean_expected_payout(st, pod))
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(key=pl.key, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate))


def sell_for(x: Ctx, shape) -> dict | None:
    trio_ok = False
    if shape.type_label == "A":
        r = run_plan(x, shape, TL.PLANS["A_trio"])
        trio_ok = bool(r and r["gate"])
    plans = TL.sell_plans_for(shape.type_label, 7, x.rtype,
                              pw_ent=shape.pw_ent, trio_ok=trio_ok)
    if not plans:
        return None
    r = run_plan(x, shape, plans[0])
    if r is None or not r["gate"]:
        return None
    return r


def summarize(recs):
    if not recs:
        return dict(n=0)
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    shown = (len(hits) - len(gami)) / len(recs) * 100
    return dict(n=len(recs), inv=inv, pay=pay, roi=pay / inv * 100 if inv else 0.0,
               hit=len(hits) / len(recs) * 100, shown=shown,
               med_pay=float(np.median([r["pay"] for r in hits])) if hits else 0.0)


def main():
    explore_idx = C.select(None, "explore")
    conc_edges, cell_delta, conc_delta_1d, band_delta_1d = fit_deltas(explore_idx)

    # 較正後 axis_sum の分布で、raw の堅い割合(axis_sum>=1.44)と同じ割合になる境界を
    # explore窓だけで決め、両窓に固定値として使う（境界も vintage）。
    raw_firm_rate = float((Z["AXIS_SUM"][explore_idx] >= TL.AXIS_SUM_FIRM).mean())
    cal_vals = []
    for i in explore_idx:
        p3 = Z["P3"][i]
        if not np.isfinite(p3).all():
            continue
        order = np.argsort(-p3)
        p3_cal = calibrated_top3_dict(i, conc_edges, cell_delta, conc_delta_1d, band_delta_1d)
        cal_vals.append(p3_cal[int(order[0]) + 1] + p3_cal[int(order[1]) + 1])
    thr_cal = float(np.quantile(cal_vals, 1 - raw_firm_rate))
    print(f"raw firm率(explore)={raw_firm_rate*100:.2f}%  →  cal境界={thr_cal:.4f}"
          f"（raw境界1.44の代わりに使う・explore窓だけで決定・両窓固定）")

    for window in ("explore", "confirm"):
        idx = C.select(None, window)
        old_recs, new_recs = [], []
        n_flip = 0
        flip_dirs = {"loose_to_firm": 0, "firm_to_loose": 0}
        for i in idx:
            x = build_ctx(i, conc_edges, cell_delta, conc_delta_1d, band_delta_1d, thr_cal)
            if x is None:
                continue
            if x.shape_raw.firm == x.shape_cal.firm:
                continue
            n_flip += 1
            if x.shape_cal.firm and not x.shape_raw.firm:
                flip_dirs["loose_to_firm"] += 1
            else:
                flip_dirs["firm_to_loose"] += 1
            r_old = sell_for(x, x.shape_raw)
            r_new = sell_for(x, x.shape_cal)
            if r_old:
                old_recs.append(r_old)
            if r_new:
                new_recs.append(r_new)
        print(f"\n=== {window} 窓 ===  型(firm/loose)が入れ替わったレース n={n_flip}  {flip_dirs}")
        so, sn = summarize(old_recs), summarize(new_recs)
        print(f"  現行(raw type) で売った場合   : n={so.get('n',0):3d}  的中={so.get('hit',0):5.1f}%"
              f"  表示的中={so.get('shown',0):5.1f}%  ROI={so.get('roi',0):5.1f}%  払戻中央={so.get('med_pay',0):,.0f}")
        print(f"  較正型(cal type) で売った場合  : n={sn.get('n',0):3d}  的中={sn.get('hit',0):5.1f}%"
              f"  表示的中={sn.get('shown',0):5.1f}%  ROI={sn.get('roi',0):5.1f}%  払戻中央={sn.get('med_pay',0):,.0f}")


if __name__ == "__main__":
    main()
