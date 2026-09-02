#!/usr/bin/env python3
"""軸信頼ゲートは今も妥当か（2026-09-03・ユーザー質問「ゲートの上と下で信頼度を比較して」）。

## 何を確かめるか

ゲートは各プランの中で `axis_sum` 下位1/5を外す（`AXIS_GATE_MIN`）。
導入時（2026-08-28）の根拠は **外した側 10.49件/日・表示的中 18.59%・ROI 67.8%** で、
通した側との差 +3.1pt [+1.3,+4.8]・無作為対照 20/20 勝ちだった。

その後 **#441（特選を看板枠から外す）・型C の帯 20→15倍・型A の3分割** が入って
商品構成が変わっている。**当時の根拠がまだ立つのかを測り直す。**

    ① 通過側 ↔ 落ちた側 の 表示的中・ROI・そろい率・換金率（両窓）
    ② 差の95%CI（日単位ブートストラップ）
    ③ プラン別に両窓で符号が一致するか
    ④ プラン内 `axis_sum` 五分位で単調か（＝量として意味があるか）

🔴 ROI では採否を決めない。表示的中で見る。
🔴 両窓で符号が一致しないものは根拠にしない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/axis_gate_audit.py
"""
from __future__ import annotations

import importlib.util
import itertools
import random
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
GATE = _G.AXIS_GATE_MIN
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
    top3 = set(C3[int(a["TRIO_WIN"][i])])
    return dict(shape=shape, po=po, pr=pr, po3=po3, pr3=pr3, tl=tl,
                rt=str(a["RTYPE"][i]), date=str(a["DATE"][i]),
                axis=float(a["AXIS_SUM"][i]),
                both=(order[0] in top3 and order[1] in top3),
                win=PERMS[int(a["WIN"][i])], pay=float(a["PAY"][i]) / 100.0,
                win3=frozenset(C3[int(a["TRIO_WIN"][i])]),
                odds3=float(a["TRIO_PAY"][i]))


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
                mean=mean_expected_payout(stk, pod), both=x["both"],
                shown=pay > float(sum(stk.values())))


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


def stat(rs):
    if not rs:
        return None
    nb = sum(r["both"] for r in rs)
    return dict(n=len(rs), shown=sum(r["shown"] for r in rs) / len(rs) * 100,
                roi=sum(r["pay"] for r in rs) / sum(r["inv"] for r in rs) * 100,
                both=nb / len(rs) * 100,
                conv=(sum(1 for r in rs if r["both"] and r["shown"]) / nb * 100) if nb else 0.0,
                med=st.median([r["pay"] for r in rs if r["pay"] > r["inv"]] or [0]),
                big=sum(1 for r in rs if r["pay"] >= 100_000))


def boot(days, n=2000, seed=0):
    rnd = random.Random(seed)
    ks = list(days)
    out = []
    for _ in range(n):
        smp = [ks[rnd.randrange(len(ks))] for _ in range(len(ks))]
        a0 = sum(days[d][0] for d in smp); h0 = sum(days[d][1] for d in smp)
        a1 = sum(days[d][2] for d in smp); h1 = sum(days[d][3] for d in smp)
        if a0 and a1:
            out.append((h0 / a0 - h1 / a1) * 100)
    out.sort()
    return out[int(len(out) * .025)], out[int(len(out) * .975)]


def main() -> None:
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    for lab, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        nd = C.days_of(C.select(None, win))
        up, dn, byplan = [], [], defaultdict(lambda: ([], []))
        for i in C.select(None, win):
            i = int(i)
            if tp[i] not in "ABCDEF":
                continue
            x = prep(i)
            if not x:
                continue
            k = sell_key(x)
            r = build(x, k)
            if not r:
                continue
            (up if x["axis"] >= GATE.get(k, 0.0) else dn).append(r)
            byplan[k][0 if x["axis"] >= GATE.get(k, 0.0) else 1].append(r)
        a, b = stat(up), stat(dn)
        days = defaultdict(lambda: [0, 0, 0, 0])
        for r in up:
            days[r["date"]][0] += 1; days[r["date"]][1] += r["shown"]
        for r in dn:
            days[r["date"]][2] += 1; days[r["date"]][3] += r["shown"]
        lo, hi = boot(days)
        print("")
        print("=" * 104)
        print(f"=== {lab}  日数={nd} ===")
        print(f"  {'':12s} {'件/日':>6s} {'表示的中':>8s} {'ROI':>7s} {'そろい率':>8s} "
              f"{'換金率':>7s} {'払戻中央':>9s} {'10万+':>6s}")
        for name, s in (("通過（売る）", a), ("落ちた（外す）", b)):
            print(f"  {name:12s} {s['n']/nd:6.2f} {s['shown']:7.2f}% {s['roi']:6.1f}% "
                  f"{s['both']:7.1f}% {s['conv']:6.1f}% {s['med']:9,.0f} {s['big']:6d}")
        print(f"  → 表示的中の差 {a['shown']-b['shown']:+.2f}pt  95%CI [{lo:+.2f},{hi:+.2f}]"
              + ("  ← 0 を跨がない" if lo * hi > 0 else "  ← **0 を跨ぐ**"))
        print(f"  {'プラン別':12s} {'通過 件':>7s} {'表示的中':>8s} | {'落ちた 件':>8s} "
              f"{'表示的中':>8s} | {'差':>7s}")
        for k in sorted(byplan):
            u, d = byplan[k]
            if len(u) < 30 or len(d) < 20:
                continue
            su, sd = stat(u), stat(d)
            print(f"  {k:12s} {su['n']:7d} {su['shown']:7.2f}% | {sd['n']:8d} "
                  f"{sd['shown']:7.2f}% | {su['shown']-sd['shown']:+6.2f}pt")


if __name__ == "__main__":
    main()
