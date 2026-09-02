#!/usr/bin/env python3
"""型B/C/E に「三連複へ振り替える」ハイブリッドを入れられるか（2026-09-02・ユーザー観察）。

## 発端

> 今日の的中が上がってこない。高額が惜しく外れたものはいくつかあるが、
> F の多さ、BCDE の的中不足の印象。

2026-09-02 の実測（売った37件・行に焼き付いた legs と確定オッズだけで採点）:

    軸2車そろい 16件(43%)  … うち 的中4 / **順序違い9** / 相手外し3
    片軸のみ    16件(43%)
    軸崩壊       5件(14%)  … 軸2車前提の商品では構造的に取れない

**同じ買い目を三連複に畳むと 4件 → 13件・ROI 34.3% → 54.2%**。
ただしこれは1日の後知恵なので、両窓で測ってから採否を決める。

## 既にわかっていること（再検証しない）

- 🔴 **全面的な三連複化は負ける**（`keirin-typef-hit-and-nearmiss-2026-08-31`）。
  すり抜けは本物（A_hit 28% / C_hit 34%）だが振り替えると ROI −5〜10pt。
- 🟢 **ゲートを通る場合だけ振り替えるハイブリッドは型Aで両窓有意**
  （`keirin-type-a-trio-hybrid-2026-08-31`）。件数不変・ROI不変・表示的中
  +0.81〜+2.67 / +1.75〜+4.20pt。**これは既に `A_trio` として本番に入っている。**
  三連単÷三連複は6倍ではなく**中央2.5倍・順当帯では1.5倍**なので、
  点数が減るぶんで払戻の目減りを相殺できるのが機序。

→ **本稿の問いは「同じ形を B / C / E へ広げられるか」だけ。**

## 作法

🔴 買い目は本番の `build_legs` / `allocate` で組む。三連複化は**買った三連単の
   異なる組**に畳み、ダッチ配分（本番の `A_trio` と同じ `alloc="dutch"`）。
🔴 入稿ゲート（平均想定払戻 > 2万・1点でも予測 < 2.0倍なら見送り）は
   **三連複側にも当てる**。通らなければ三連単のまま（＝件数は減らない）。
🔴 台の PO は `odds_tf_n7`（train_end 2025-12-31）＝**探索窓は in-sample**。
   採否は確認窓で決め、探索窓は符号の一致確認にだけ使う。
🔴 ROI では採否を決めない。**表示的中**で見る（この層は ROI を ±2.5pt に
   収めるのに約15.6年）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/trio_hybrid.py
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
    BUDGET, PLANS, Plan, RaceShape, allocate, build_legs, mean_expected_payout,
    win_entropy)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS, C3 = C.CANON, C.CANON3
C3IDX = C.C3IDX

#: 三連複へ畳む対象。`A_trio` は既に本番にあるので比較用に並べる。
TARGETS = {"A": "A_hit", "B": "B_hit", "C": "C_hit", "E": "E_hit"}

_A = None


def arrays():
    global _A
    if _A is None:
        z = C.board()
        _A = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "PO", "WIN", "PAY",
                                "TRIO_PO", "TRIO_ODDS", "TRIO_WIN", "TRIO_PAY",
                                "DATE", "TYPE", "AXIS_SUM", "ARARE", "GAP")}
    return _A


def race(i: int):
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
    shape = RaceShape(str(a["TYPE"][i]), float(a["AXIS_SUM"][i]), int(a["ARARE"][i]),
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
    return dict(shape=shape, po=po, pr=pr, po3=po3, pr3=pr3,
                win=PERMS[int(a["WIN"][i])], pay=float(a["PAY"][i]) / 100.0,
                win3=frozenset(C3[int(a["TRIO_WIN"][i])]),
                odds3=float(a["TRIO_PAY"][i]), date=str(a["DATE"][i]))


def _score(st, pod, win, odds, trio: bool):
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    pay = float(st[win] * odds) if win in st else 0.0
    if not trio:
        pay = float(st[win] / 100.0 * odds * 100.0) if win in st else 0.0
    return dict(date="", inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod))


def arm(x, plan_key: str, mode: str):
    """mode: 'tf'=現行 / 'trio'=常に三連複 / 'hybrid'=通れば三連複・落ちたら三連単。"""
    plan = PLANS[plan_key]
    legs = build_legs(x["shape"], plan, x["po"], x["pr"])
    if not legs:
        return None
    tf = None
    st = allocate(legs, x["po"], x["pr"], plan)
    if st:
        tf = _score(st, x["po"], x["win"], x["pay"], trio=False)
    if mode == "tf":
        r = tf
        if r:
            r["date"] = x["date"]
        return r
    tri = sorted({frozenset(c) for c in legs}, key=lambda s: sorted(s))
    tri = [c for c in tri if c in x["po3"]]
    tp = None
    if len(tri) >= 2:
        p3plan = Plan("t", plan.type_label, "trio", "axis2_flow", len(tri), alloc="dutch")
        st3 = allocate(tri, x["po3"], x["pr3"], p3plan)
        if st3:
            tp = _score(st3, x["po3"], x["win3"], x["odds3"], trio=True)
    r = tp if mode == "trio" else (tp or tf)
    if r:
        r["date"] = x["date"]
    return r


def main() -> None:
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = np.array([float(v) for v in z["AXIS_SUM"]])
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        nd = C.days_of(C.select(None, win))
        print("")
        print("=" * 112)
        print(f"=== {label}  日数={nd} ===")
        for t, pk in TARGETS.items():
            idx = [int(i) for i in C.select(t, win)
                   if axs[int(i)] >= AXIS_GATE_MIN.get(pk, 0.0)]
            recs = {m: [] for m in ("tf", "trio", "hybrid")}
            for i in idx:
                x = race(i)
                if x is None:
                    continue
                for m in recs:
                    r = arm(x, pk, m)
                    if r:
                        recs[m].append(r)
            print("")
            print(f"  型{t}（{pk}・n={len(idx)}）")
            print(C.HEAD)
            for m, name in (("tf", "現行（三連単）"), ("trio", "常に三連複"),
                            ("hybrid", "ハイブリッド（通れば三複）")):
                print(C.line(name, C.summarize(recs[m], nd)))


if __name__ == "__main__":
    main()
