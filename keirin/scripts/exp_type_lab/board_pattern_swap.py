#!/usr/bin/env python3
"""軸2車が**別ライン**のとき、軸をラインから取り直す（9車 `F_line` を7車へ・2026-09-10）。

`nine_car_type_f_2026_09_06.md` は 9車の型F で
「軸2車を **p3合計最大ラインの上位2車** から取り、三連複を計画払戻2万円まで積む」
（`line_axis2_flow`）が両窓で表示的中 +2.90 / +7.92pt になることを示し、
**7車では測っていない**（「車数をまたいだ移植でこのリポジトリは繰り返し失敗している」）。

本稿の記述（軸2車が別ラインだと、本番確率が示すより 3〜4.8pt そろわない）は、
その移植を試す根拠になる。**件数を減らさない**買い方の切り替えなので、
レース選別（35%を捨て看板が半減する）より筋がよい。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, RaceShape, allocate, build_legs, mean_expected_payout,
    _lines_of, _strongest_pair, win_entropy)
from typef_racetype import _plan_for  # noqa: E402

PERMS, C3 = C.CANON, C.CANON3
CIDX = {c: i for i, c in enumerate(PERMS)}
MIN_MEAN, MIN_ODDS = 20_000.0, 2.0
AXIS_GATE_MIN = {"A_hit": 1.537, "D_hit": 1.263, "E_hit": 1.245, "F_hit": 1.230}
HIT_PLANS = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"}
SIGN_RT = ("決勝", "S級決勝", "A級決勝", "ガールズ決勝", "準決勝", "S級準決勝",
           "A級準決勝", "ガールズ準決勝")


def _run(shape, key, pod_tf, prb_tf, pod_t3, prb_t3, win_tf, pay_tf,
         win_t3, odds_t3):
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = (pod_t3, prb_t3) if trio else (pod_tf, prb_tf)
    legs = build_legs(shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if mean_expected_payout(st, pod) <= MIN_MEAN:
        return None
    if min(float(pod[c]) for c in st) < MIN_ODDS:
        return None
    if trio:
        pay = float(st[win_t3] * odds_t3) if win_t3 in st else 0.0
    else:
        pay = float(st[win_tf] / 100.0 * pay_tf * 100.0) if win_tf in st else 0.0
    return dict(inv=float(sum(st.values())), pay=pay, k=len(st))


def build():
    z = C.board()
    d = {k: z[k] for k in z.files}
    idx = C.select(window="all")
    rows = []
    cars = list(range(1, 8))
    for i in idx:
        p3 = {c: float(d["P3"][i][c - 1]) for c in cars}
        pw = {c: float(d["PW"][i][c - 1]) for c in cars}
        lg = {c: str(d["LG"][i][c - 1]) for c in cars}
        lp = {c: int(d["A_line_pos"][i][c - 1]) for c in cars}
        pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
        pod_tf = {PERMS[t]: float(d["PO"][i][t]) for t in range(210)
                  if np.isfinite(d["PO"][i][t]) and d["PO"][i][t] > 0}
        if len(pod_tf) < 60:
            continue
        pod_t3 = {frozenset(c): float(d["TRIO_PO"][i][j]) for j, c in enumerate(C3)
                  if np.isfinite(d["TRIO_PO"][i][j]) and d["TRIO_PO"][i][j] > 0}
        prb_t3 = {frozenset(c): sum(pr.get(p, 0.0)
                                    for p in itertools.permutations(c))
                  for c in C3}
        order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
        lines = _lines_of(lg, lp)
        shape = RaceShape(str(d["TYPE"][i]), float(d["AXIS_SUM"][i]),
                          int(d["ARARE"][i]), float(d["GAP"][i]), False, order,
                          win_entropy(pw), lines, _strongest_pair(lines, p3))
        rt = str(d["RTYPE"][i])
        trio_ok = _run(shape, "A_trio", pod_tf, pr, pod_t3, prb_t3,
                       PERMS[int(d["WIN"][i])], float(d["PAY"][i]) / 100.0,
                       frozenset(C3[int(d["TRIO_WIN"][i])]),
                       float(d["TRIO_PAY"][i])) is not None
        key = _plan_for(shape.type_label, rt, shape.pw_ent, trio_ok, SIGN_RT)
        if key not in HIT_PLANS:
            continue
        if float(shape.axis_sum) < AXIS_GATE_MIN.get(key, 0.0):
            continue
        args = (pod_tf, pr, pod_t3, prb_t3, PERMS[int(d["WIN"][i])],
                float(d["PAY"][i]) / 100.0, frozenset(C3[int(d["TRIO_WIN"][i])]),
                float(d["TRIO_PAY"][i]))
        cur = _run(shape, key, *args)
        if cur is None:
            continue
        alt = _run(shape, "F_line", *args)
        ax_same = lg[order[0]] == lg[order[1]]
        rows.append(dict(date=str(d["DATE"][i]), key=key,
                         win=("explore" if str(d["DATE"][i]) <= "2025-12-31"
                              else "confirm"),
                         ax_same=bool(ax_same), cur=cur, alt=alt))
    with open("/tmp/board_swap_rows.pkl", "wb") as f:
        pickle.dump(rows, f)
    print("rows", len(rows), " alt組めた",
          sum(1 for r in rows if r["alt"]), " 別ライン",
          sum(1 for r in rows if not r["ax_same"]))


def kpi(items):
    days = len({d for d, _ in items})
    r = [x for _, x in items]
    pays = [x["pay"] for x in r]
    sh = [x for x in r if x["pay"] > x["inv"]]
    hits = [x["pay"] for x in r if x["pay"] > 0]
    return dict(n=len(r), per_day=len(r) / days, shown=100 * len(sh) / len(r),
                roi=100 * sum(pays) / sum(x["inv"] for x in r),
                med=median(hits) if hits else 0.0,
                p100k=sum(1 for p in pays if p >= 100_000) / days)


def show(name, k):
    print(f"  {name:<38} 件/日 {k['per_day']:>5.2f}  表示的中 {k['shown']:>5.2f}%"
          f"  ROI {k['roi']:>5.1f}  払戻中央 {k['med']:>7.0f}"
          f"  10万+/日 {k['p100k']:.3f}  n={k['n']}")


def run():
    rows = pickle.load(open("/tmp/board_swap_rows.pkl", "rb"))
    for w in ("explore", "confirm"):
        rr = [r for r in rows if r["win"] == w]
        cand = [r for r in rr if (not r["ax_same"]) and r["alt"]]
        print(f"\n== {w} ==  母集団 {len(rr)}  別ライン∧altが組める {len(cand)}"
              f" ({100*len(cand)/len(rr):.1f}%)")
        base = [(r["date"], r["cur"]) for r in rr]
        swap = [(r["date"], r["alt"] if ((not r["ax_same"]) and r["alt"])
                 else r["cur"]) for r in rr]
        show("現行", kpi(base))
        show("別ラインだけ F_line 型（三連複）へ", kpi(swap))
        show("  （切替対象だけ）現行", kpi([(r["date"], r["cur"]) for r in cand]))
        show("  （切替対象だけ）F_line 型", kpi([(r["date"], r["alt"]) for r in cand]))
        # 無作為対照: 同数のレースを無作為に選んで F_line へ切り替える
        pool = [j for j, r in enumerate(rr) if r["alt"]]
        outs = []
        for s in range(20):
            rng = np.random.default_rng(s)
            pick = set(rng.choice(pool, size=min(len(cand), len(pool)),
                                  replace=False).tolist())
            outs.append(kpi([(r["date"], r["alt"] if j in pick else r["cur"])
                             for j, r in enumerate(rr)]))
        m = float(np.median([o["shown"] for o in outs]))
        wins = sum(1 for o in outs if kpi(swap)["shown"] > o["shown"])
        print(f"  {'無作為に同数を切替（20seed 中央）':<38} 表示的中 {m:>5.2f}%"
              f"   → 提案の勝ち {wins}/20")


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build()
    else:
        run()
