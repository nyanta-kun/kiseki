#!/usr/bin/env python3
"""件数を増やしたいときに動かせるダイヤルを測る（2026-09-03・ユーザー要望）。

> もう少しレース数を取りたい

いま件数を削っているのは3つ。**2026-09-02（7車71R）の実測**:

    入稿             35件（朝）→ 昼夕で拾い直して日合計 43件
    axis_gate        17件（朝）  軸信頼ゲート＝各プラン内の下位1/5を外す
    gate_mean_payout  8件（朝）  平均想定払戻 2万円（＝商品の約束そのもの）
    daily_cap        11件（朝）  日次上限。ただし**8件は昼夕で拾い直した**＝実質3件

🔴 **上限は「捨てる」のではなく「遅らせる」**（次の波で再判定される）ので、
   件数への効き目は小さい。実際に件数を決めているのは **軸信頼ゲート**。

## 測る腕

    ① 現行
    ② 軸信頼ゲートを外す
    ③ 軸信頼ゲートを下位1/10へ緩める（p20 → p10 相当）

🔴 **件数が変わるので無作為対照を置く**（`docs/RECOMMENDATION.md` §6）。
   ②③は「同じだけ件数を増やしたら表示的中がどうなるか」の対照が必要だが、
   ここでは**増える側の成績を直接出す**（増分そのものを見るほうが判断しやすい）。
🔴 ROI では採否を決めない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/volume_levers.py
"""
from __future__ import annotations

import importlib.util
import itertools
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import scripts.exp_type_lab.common as C  # noqa: E402
from src.type_lab import (  # noqa: E402
    ANA_PW_ENT_MIN, PLANS, RaceShape, SIGNBOARD_RACE_TYPES,
    TYPE_F_SELL_BY_RACE_TYPE, TYPE_F_SELL_DEFAULT, allocate, build_legs,
    mean_expected_payout, win_entropy)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
GATE, QUANT = _G.AXIS_GATE_MIN, _G.AXIS_PRIORITY_QUANTILES
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS, C3 = C.CANON, C.CANON3
_A = None


def arrays():
    global _A
    if _A is None:
        z = C.board()
        _A = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "PO", "WIN", "PAY",
                                "TRIO_PO", "TRIO_ODDS", "TRIO_WIN", "TRIO_PAY",
                                "DATE", "TYPE", "AXIS_SUM", "ARARE", "GAP", "RTYPE")}
    return _A


def prep(i: int):
    a = arrays()
    cars = list(range(1, 8))
    p3 = {c: float(a["P3"][i][c - 1]) for c in cars}
    pw = {c: float(a["PW"][i][c - 1]) for c in cars}
    from src.strategy_wt import rank_7t3_blend_probs
    pr = rank_7t3_blend_probs(cars, pw, p3,
                              line_group={c: a["LG"][i][c - 1] for c in cars},
                              line_pos={c: a["A_line_pos"][i][c - 1] for c in cars})
    po = {PERMS[t]: float(a["PO"][i][t]) for t in range(210)
          if np.isfinite(a["PO"][i][t]) and a["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    tl = str(a["TYPE"][i])
    shape = RaceShape(tl, float(a["AXIS_SUM"][i]), int(a["ARARE"][i]),
                      float(a["GAP"][i]), False, order, win_entropy(pw))
    po3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C3)
           if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    pr3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
           for c in C3}
    return dict(shape=shape, po=po, pr=pr, po3=po3, pr3=pr3, tl=tl,
                rt=str(a["RTYPE"][i]), date=str(a["DATE"][i]),
                axis=float(a["AXIS_SUM"][i]),
                win=PERMS[int(a["WIN"][i])], pay=float(a["PAY"][i]) / 100.0,
                win3=frozenset(C3[int(a["TRIO_WIN"][i])]),
                odds3=float(a["TRIO_PAY"][i]))


def sell_key(x) -> str:
    tl = x["tl"]
    if tl == "F":
        return ("F_sign" if x["rt"] in SIGNBOARD_RACE_TYPES
                else TYPE_F_SELL_BY_RACE_TYPE.get(x["rt"], TYPE_F_SELL_DEFAULT))
    if tl == "A":
        if x["shape"].pw_ent >= ANA_PW_ENT_MIN:
            return "A_ana"
        return "A_trio" if build(x, "A_trio") else "A_hit"
    return {"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]


def build(x, key):
    plan = PLANS[key]
    pod, prb = ((x["po3"], x["pr3"]) if plan.bet_type == "trio" else (x["po"], x["pr"]))
    legs = build_legs(x["shape"], plan, pod, prb)
    if not legs:
        return None
    stk = allocate(legs, pod, prb, plan)
    if not stk:
        return None
    if mean_expected_payout(stk, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in stk) < MIN_POINT_ODDS:
        return None
    trio = plan.bet_type == "trio"
    win, odds = ((x["win3"], x["odds3"]) if trio else (x["win"], x["pay"]))
    pay = (float(stk[win] * odds) if trio else float(stk[win] / 100.0 * odds * 100.0)) \
        if win in stk else 0.0
    return dict(date=x["date"], inv=float(sum(stk.values())), pay=pay, k=len(stk),
                mean=mean_expected_payout(stk, pod))


#: プランごとの「ゲート前」母集団の分位。探索窓で作る。
#: 🔴 `AXIS_PRIORITY_QUANTILES` から p10 は取れない。あれは**ゲート後**の
#:    母集団の分位で、`qs[0]`（p0）がゲート閾値そのものになっている
#:    （F_hit: qs[0]=1.2300 == AXIS_GATE_MIN["F_hit"]）。使うと逆に厳しくなる。
P10: dict[str, float] = {}


def fit_p10(pre) -> None:
    by = defaultdict(list)
    for x in pre:
        if x["rec"]:
            by[x["key"]].append(x["axis"])
    for k, v in by.items():
        v.sort()
        P10[k] = v[max(0, int(len(v) * 0.10) - 1)]


def thr_for(key: str, level: str) -> float:
    """軸信頼ゲートの閾値。'p20'=現行 / 'off'=無し / 'p10'=下位1/10だけ外す。"""
    if level == "off":
        return 0.0
    if level == "p20":
        return GATE.get(key, 0.0)
    return P10.get(key, GATE.get(key, 0.0))


def main() -> None:
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    for lab, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        pre = []
        for i in C.select(None, win):
            i = int(i)
            if tp[i] not in "ABCDEF":
                continue
            x = prep(i)
            if x:
                x["key"] = sell_key(x)
                x["rec"] = build(x, x["key"])
                pre.append(x)
        nd = C.days_of(C.select(None, win))
        if win == "explore":
            fit_p10(pre)          # 🔴 閾値は探索窓だけで作る（確認窓へ持ち込まない）
        print("")
        print("=" * 118)
        print(f"=== {lab}  日数={nd} ===")
        print(C.HEAD)
        base = None
        for level, name in (("p20", "① 現行（下位1/5を外す）"),
                            ("p10", "③ 下位1/10へ緩める"),
                            ("off", "② 軸信頼ゲートを外す")):
            recs = [x["rec"] for x in pre
                    if x["rec"] and x["axis"] >= thr_for(x["key"], level)]
            s = C.summarize(recs, nd)
            print(C.line(name, s) + f"  10万+/日 {s['big_per_day']:.3f}")
            if level == "p20":
                base = {id(r) for r in recs}
            else:
                add = [r for r in recs if id(r) not in base]
                if add:
                    t = C.summarize(add, nd)
                    print(f"      └ 増える分だけ {t['perday']:5.2f}件/日  "
                          f"表示的中 {t['shown']:5.2f}%  ROI {t['roi']:5.1f}%  "
                          f"払戻中央 {t['med_pay']:7,.0f}円")


if __name__ == "__main__":
    main()
