#!/usr/bin/env python3
"""的中体験を上げる手を積み上げて測る（2026-09-03）。

`hit_conditions.py` の分解で見えたこと（両窓一致・売る商品 探索18,549 / 確認7,496件）:

    全体      そろい 53.9/52.7%  ×  換金(そろい時) 38.8/39.9%  → 表示的中 24.62/25.04%

    プラン       そろい率        換金率(そろい時)   表示的中
    A_hit     72.1/71.3      49.0/50.4       35.80/36.03
    A_trio    57.9/54.1      53.8/55.8       31.15/30.20
    F_hit     37.1/36.9      55.6/54.8       27.20/27.24   ← 換金が最も上手い
    B_hit     65.2/65.7      40.4/39.3       27.66/27.68
    C_hit     63.6/60.8      33.8/38.3       26.34/28.95
    D_hit     46.6/44.4      51.2/57.5       23.87/25.53
    **E_hit** 44.1/43.1      **24.8/28.2**   19.29/21.44   ← D と同じそろい率で換金が半分
    **A_ana** 55.1/55.3      **0.0 / 0.0**    5.32/ 4.72   ← 構造的に「そろうと当たらない」
    F_sign    41.1/42.8      11.0/10.4        5.51/ 5.05

🔴🔴 **`A_ana` はそろい時の換金率が定義上ゼロ**（軸1を外して買うので、軸2車が
   3着内に入ったら当たらない）。母集団の 55% がそれ。型Aの 18%（探索695/確認360件）を
   ここへ回している。**看板は `F_sign` が別に作っているので、当たらない商品を二重に持っている。**

🔴 **`E_hit` は「そろっても換金できない」プラン。** そろい率は D と同等なのに換金が半分。

## 測る腕

    ① 現行（#441 後 = 特選を看板枠から外した状態）
    ② + 三連複ハイブリッド（ゲートを通れば三連複へ振替・A/B/C/E）
    ③ + A_ana を A_hit へ戻す
    ④ + E_hit を三連複4点へ
    ⑤ 全部

🔴 件数はどの腕でも変わらない（振り分けの入れ替えだけ）ので無作為対照は要らないが、
   **両窓で符号が一致しないものは採らない**。
🔴 ROI では採否を決めない。動くのは表示的中・払戻中央・10万+。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/hit_menu.py
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import scripts.exp_type_lab.common as C  # noqa: E402
from src.type_lab import (  # noqa: E402
    ANA_PW_ENT_MIN, PLANS, Plan, RaceShape, SIGNBOARD_RACE_TYPES,
    TYPE_F_SELL_BY_RACE_TYPE, TYPE_F_SELL_DEFAULT, allocate, build_legs,
    mean_expected_payout, win_entropy)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS, C3 = C.CANON, C.CANON3

#: 型E を三連複4点にする腕。`type_e_2026_09_01.md` が測った案と同じ形
#: （帯は掛けず確率上位・ダッチ）。
E_TRIO = Plan("E_trio", "E", "trio", "axis2_flow", 4, alloc="dutch")

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
    # 🔴 台の `TRIO_PO` は `1 / Σ_perm(1/PO)`（`build_race_type_board.py` の実装）。
    #    本番 `build_type_lab_picks._fold_to_trio` と**同じ式**で、実オッズと
    #    突き合わせても中央 0.998 と合う（2026-09-03 検証）。
    #    ⚠️ 台の**モジュール docstring だけ** `0.75 / Σ` と古い記述が残っていたので
    #       是正した。読んで「直す」と三連複の予測オッズが一律 25% 下がり、
    #       平均想定払戻ゲートが**エラー無しで別物になる**。
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


def score(x, plan, legs):
    pod, prb = ((x["po3"], x["pr3"]) if plan.bet_type == "trio" else (x["po"], x["pr"]))
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


#: 🔴 **三連複へ畳んではいけないプラン。** `F_sign`（看板枠）と `A_ana`（穴狙い）は
#:    「たまに大きく当たる」ために作ってある。順序を捨てると配当が落ちて存在理由が消える
#:    （実測: 全プランに当てると 10万+/日 が 0.194 → 0.106 と半減した）。
NO_HYBRID = {"F_sign", "A_ana"}


def run(x, plan_key, hybrid=False):
    plan = E_TRIO if plan_key == "E_trio" else PLANS[plan_key]
    pod, prb = ((x["po3"], x["pr3"]) if plan.bet_type == "trio" else (x["po"], x["pr"]))
    legs = build_legs(x["shape"], plan, pod, prb)
    if not legs:
        return None
    base = score(x, plan, legs)
    if not hybrid or plan.bet_type == "trio" or plan_key in NO_HYBRID:
        return base
    tri = sorted({frozenset(c) for c in legs}, key=lambda s: sorted(s))
    tri = [c for c in tri if c in x["po3"]]
    if len(tri) >= 2:
        tp = Plan("t", plan.type_label, "trio", "axis2_flow", len(tri), alloc="dutch")
        r = score(x, tp, tri)
        if r:
            return r
    return base


#: 腕の定義。(三連複ハイブリッド, A_ana を戻す, E を三連複4点へ)
ARMS = {
    "① 現行（#441 後）":            (False, False, False),
    "② + 三連複ハイブリッド":         (True, False, False),
    "②' ②から看板枠・穴狙いを除く":     ("keep", False, False),
    "③ + A_ana を A_hit へ":       (False, True, False),
    "④ + E を三連複4点へ":          (False, False, True),
    "⑤ ②'+③+④ 全部":             ("keep", True, True),
}


def plan_of(x, restore_ana: bool, e_trio: bool, trio_ok: bool) -> str:
    tl = x["tl"]
    if tl == "F":
        return ("F_sign" if x["rt"] in SIGNBOARD_RACE_TYPES
                else TYPE_F_SELL_BY_RACE_TYPE.get(x["rt"], TYPE_F_SELL_DEFAULT))
    if tl == "A":
        if not restore_ana and x["shape"].pw_ent >= ANA_PW_ENT_MIN:
            return "A_ana"
        return "A_trio" if trio_ok else "A_hit"
    if tl == "E":
        return "E_trio" if e_trio else "E_hit"
    return {"B": "B_hit", "C": "C_hit", "D": "D_hit"}[tl]


def boot(days, n_boot=2000, seed=0):
    """日単位ブートストラップで Δ表示的中 の 95%CI を出す（**同じ日を対で取る**）。"""
    import random
    rnd = random.Random(seed)
    ks = list(days)
    out = []
    for _ in range(n_boot):
        smp = [ks[rnd.randrange(len(ks))] for _ in range(len(ks))]
        n0 = sum(days[d][0] for d in smp); h0 = sum(days[d][1] for d in smp)
        n1 = sum(days[d][2] for d in smp); h1 = sum(days[d][3] for d in smp)
        if n0 and n1:
            out.append((h1 / n1 - h0 / n0) * 100)
    out.sort()
    return out[int(len(out) * 0.025)], out[int(len(out) * 0.975)]


def main() -> None:
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    for lab, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        nd = C.days_of(C.select(None, win))
        pre = []
        for i in idx:
            x = prep(i)
            if x:
                x["trio_ok"] = run(x, "A_trio") is not None if x["tl"] == "A" else False
                pre.append(x)
        print("")
        print("=" * 118)
        print(f"=== {lab}  n={len(pre):,}R  日数={nd} ===")
        print(C.HEAD)
        allrec = {}
        for name, (hyb, ana, et) in ARMS.items():
            recs = []
            keep = hyb == "keep"
            hyb = bool(hyb)
            for x in pre:
                key = plan_of(x, ana, et, x["trio_ok"])
                if keep and key in NO_HYBRID:
                    r = run(x, key, hybrid=False)
                    gk0 = key
                    if x["axis"] >= AXIS_GATE_MIN.get(gk0, 0.0) and r:
                        recs.append(r)
                    continue
                gk = "A_hit" if key == "A_hit" else ("E_hit" if key == "E_trio" else key)
                if x["axis"] < AXIS_GATE_MIN.get(gk, 0.0):
                    continue
                r = run(x, key, hybrid=hyb)
                if r:
                    recs.append(r)
            allrec[name] = recs
            s = C.summarize(recs, nd)
            print(C.line(name, s) + f"  10万+/日 {s['big_per_day']:.3f}")
        base = allrec["① 現行（#441 後）"]
        from collections import defaultdict
        for name in ARMS:
            if name.startswith("①"):
                continue
            days = defaultdict(lambda: [0, 0, 0, 0])
            for r in base:
                days[r["date"]][0] += 1
                days[r["date"]][1] += r["pay"] > r["inv"]
            for r in allrec[name]:
                days[r["date"]][2] += 1
                days[r["date"]][3] += r["pay"] > r["inv"]
            lo, hi = boot(days)
            d = (C.summarize(allrec[name], nd)["shown"] - C.summarize(base, nd)["shown"])
            print(f"    Δ表示的中 {name[:20]:22s} {d:+6.2f}pt  95%CI [{lo:+6.2f},{hi:+6.2f}]"
                  + ("  ← 0 を跨がない" if lo * hi > 0 else ""))


if __name__ == "__main__":
    main()
