#!/usr/bin/env python3
"""繰り返し当たる条件・外れる条件を、**2つの失敗モードに分けて**洗い直す（2026-09-03）。

## なぜ分けるのか

これまでの選別スイープ（型・種別・場・gap・合成オッズ…）は全部
**「そのレースを売るか」** を1つの指標（表示的中 or ROI）で測っていて、
軒並み無作為対照に負けてきた（`docs/RECOMMENDATION.md` §7）。

しかし的中は2段の積に分解できる:

    P(表示的中) = P(軸2車そろい) × P(買い目がカバー | そろい)
                + P(軸2車そろわず) × P(カバー | そろわず)

**この2つは逆を向きうる。** 軸がそろいやすいレース＝人気決着＝配当が安い＝
帯・ゲート・ガミで落ちる。だから片方だけ改善する条件を単一指標で測ると、
もう片方の悪化に打ち消されて「効果なし」に見える。
2026-09-02 の実測がまさにそれで、**軸は想定どおりそろった（43.2% ↔ 期待49.9%）のに
そろった16件から4件しか換金できなかった**（平常なら7〜8件）。

→ **本稿は条件ごとに「そろい率」と「換金率」を別々に出す。**
   両窓で**同じ向きに動く**ものだけを候補にする。

## 作法

🔴 売る商品は本番の `sell_plans_for` / `build_legs` / `allocate` で作り、
   軸信頼ゲート＋入稿ゲートを通してから数える（通す前の数字は全部ずれる）。
🔴 台の PO は `odds_tf_n7`（train_end 2025-12-31）＝**探索窓は in-sample**。
🔴 表示的中（払戻 > 賭け金）で見る。ROI では採否を決めない。
🔴 **両窓で符号が一致しないものは候補にしない。**

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/hit_conditions.py
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
    ANA_PW_ENT_MIN, PLANS, RaceShape, SIGNBOARD_RACE_TYPES, TYPE_F_SELL_BY_RACE_TYPE,
    TYPE_F_SELL_DEFAULT, allocate, build_legs, mean_expected_payout, win_entropy)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS, C3 = C.CANON, C.CANON3

_A = None


def arrays():
    global _A
    if _A is None:
        z = C.board()
        _A = {k: z[k] for k in (
            "P3", "PW", "LG", "A_line_pos", "A_line_size", "A_is_line_leader",
            "A_race_point", "A_n_lines", "ST", "PC", "BEHIND", "PO", "WIN", "PAY",
            "TRIO_PO", "TRIO_ODDS", "TRIO_WIN", "TRIO_PAY", "DATE", "TYPE",
            "AXIS_SUM", "ARARE", "GAP", "RTYPE", "DAYI", "VENUE", "GRADE",
            "CUPG", "AGREE")}
    return _A


def make(i: int):
    """1レース＝売る1商品。ゲートを通らなければ None。"""
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
    rt = str(a["RTYPE"][i])

    def build(key):
        plan = PLANS[key]
        pod, prb = (po3, pr3) if plan.bet_type == "trio" else (po, pr)
        legs = build_legs(shape, plan, pod, prb)
        if not legs:
            return None
        stk = allocate(legs, pod, prb, plan)
        if not stk:
            return None
        if mean_expected_payout(stk, pod) <= MIN_MEAN_PAYOUT:
            return None
        if min(float(pod[c]) for c in stk) < MIN_POINT_ODDS:
            return None
        return plan, stk, pod

    # 本番の売り分け
    if tl == "F":
        key = ("F_sign" if rt in SIGNBOARD_RACE_TYPES
               else TYPE_F_SELL_BY_RACE_TYPE.get(rt, TYPE_F_SELL_DEFAULT))
    elif tl == "A":
        key = ("A_ana" if shape.pw_ent >= ANA_PW_ENT_MIN
               else ("A_trio" if build("A_trio") else "A_hit"))
    else:
        key = {"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]
    if float(a["AXIS_SUM"][i]) < AXIS_GATE_MIN.get(key, 0.0):
        return None
    got = build(key)
    if not got:
        return None
    plan, stk, pod = got
    trio = plan.bet_type == "trio"
    win = frozenset(C3[int(a["TRIO_WIN"][i])]) if trio else PERMS[int(a["WIN"][i])]
    odds = float(a["TRIO_PAY"][i]) if trio else float(a["PAY"][i]) / 100.0
    pay = (float(stk[win] * odds) if trio else float(stk[win] / 100.0 * odds * 100.0)) \
        if win in stk else 0.0
    inv = float(sum(stk.values()))
    top3 = set(C3[int(a["TRIO_WIN"][i])])
    both = order[0] in top3 and order[1] in top3
    rp = [float(x) for x in a["A_race_point"][i] if x and float(x) > 0]
    a1 = order[0]
    return dict(
        date=str(a["DATE"][i]), plan=key, type=tl, shown=pay > inv, both=both,
        inv=inv, pay=pay,
        rtype=rt, dayi=int(a["DAYI"][i]), venue=str(a["VENUE"][i]),
        grade=str(a["GRADE"][i]), cupg=int(a["CUPG"][i]) if a["CUPG"][i] else 0,
        agree=bool(a["AGREE"][i]), axis_sum=float(a["AXIS_SUM"][i]),
        gap=float(a["GAP"][i]), arare=int(a["ARARE"][i]), pw_ent=shape.pw_ent,
        rp_sd=(st.pstdev(rp) if len(rp) > 1 else 0.0),
        n_lines=int(a["A_n_lines"][i][a1 - 1] or 0),
        a1_line_size=int(a["A_line_size"][i][a1 - 1] or 0),
        a1_is_leader=bool(a["A_is_line_leader"][i][a1 - 1]),
        a1_style=str(a["ST"][i][a1 - 1]), a1_class=str(a["PC"][i][a1 - 1]),
        synth=1.0 / sum(1.0 / float(pod[c]) for c in stk), k=len(stk),
    )


def qbucket(vals, x, n=5):
    qs = st.quantiles(vals, n=n)
    for j, q in enumerate(qs):
        if x < q:
            return j
    return n - 1


CONDS = {
    "型": lambda r: r["type"],
    "プラン": lambda r: r["plan"],
    "開催日目": lambda r: min(r["dayi"], 4),
    "レース種別": lambda r: r["rtype"],
    "グレード": lambda r: r["grade"],
    "開催グレード": lambda r: r["cupg"],
    "印一致": lambda r: r["agree"],
    "軸1がライン先頭": lambda r: r["a1_is_leader"],
    "軸1のライン人数": lambda r: min(r["a1_line_size"], 4),
    "ライン本数": lambda r: min(r["n_lines"], 5),
    "軸1の脚質": lambda r: r["a1_style"],
    "軸1の級班": lambda r: r["a1_class"],
    "荒れ度": lambda r: max(-2, min(r["arare"], 3)),
}
QCONDS = {"axis_sum": "axis_sum", "gap": "gap", "pw_ent": "pw_ent",
          "rp_sd": "rp_sd", "合成オッズ": "synth", "点数": "k"}


def summarize(rows):
    n = len(rows)
    if not n:
        return None
    nb = sum(r["both"] for r in rows)
    hb = sum(1 for r in rows if r["both"] and r["shown"])
    hn = sum(1 for r in rows if not r["both"] and r["shown"])
    return dict(n=n, both=nb / n * 100,
                conv=(hb / nb * 100) if nb else 0.0,
                conv_no=(hn / (n - nb) * 100) if n - nb else 0.0,
                shown=(hb + hn) / n * 100,
                roi=sum(r["pay"] for r in rows) / sum(r["inv"] for r in rows) * 100)


def main() -> None:
    data = {}
    for lab, win in (("探索", "explore"), ("確認", "confirm")):
        rows = []
        for i in C.select(None, win):
            r = make(int(i))
            if r:
                rows.append(r)
        data[lab] = rows
        s = summarize(rows)
        print(f"[{lab}] 売る商品 {s['n']:,}件  そろい {s['both']:.1f}%  "
              f"換金(そろい時) {s['conv']:.1f}%  換金(崩れ時) {s['conv_no']:.1f}%  "
              f"表示的中 {s['shown']:.2f}%  ROI {s['roi']:.1f}%")

    for name, fn in CONDS.items():
        print("")
        print(f"── {name} " + "─" * (92 - len(name)))
        print(f"  {'値':22s} {'件(探/確)':>13s} {'そろい率 探/確':>17s} "
              f"{'換金率(そろい時) 探/確':>22s} {'表示的中 探/確':>17s}")
        keys = sorted({fn(r) for r in data["探索"]} | {fn(r) for r in data["確認"]},
                      key=str)
        for k in keys:
            a = summarize([r for r in data["探索"] if fn(r) == k])
            b = summarize([r for r in data["確認"] if fn(r) == k])
            if not a or not b or a["n"] < 100 or b["n"] < 50:
                continue
            print(f"  {str(k):22s} {a['n']:6d}/{b['n']:5d} "
                  f"{a['both']:8.1f}/{b['both']:6.1f}% "
                  f"{a['conv']:12.1f}/{b['conv']:6.1f}% "
                  f"{a['shown']:9.2f}/{b['shown']:6.2f}%")

    for name, key in QCONDS.items():
        vals = [r[key] for r in data["探索"]]
        print("")
        print(f"── {name}（探索窓の五分位） " + "─" * (72 - len(name)))
        print(f"  {'分位':22s} {'件(探/確)':>13s} {'そろい率 探/確':>17s} "
              f"{'換金率(そろい時) 探/確':>22s} {'表示的中 探/確':>17s}")
        for q in range(5):
            a = summarize([r for r in data["探索"] if qbucket(vals, r[key]) == q])
            b = summarize([r for r in data["確認"] if qbucket(vals, r[key]) == q])
            if not a or not b:
                continue
            print(f"  Q{q+1:<21d} {a['n']:6d}/{b['n']:5d} "
                  f"{a['both']:8.1f}/{b['both']:6.1f}% "
                  f"{a['conv']:12.1f}/{b['conv']:6.1f}% "
                  f"{a['shown']:9.2f}/{b['shown']:6.2f}%")


if __name__ == "__main__":
    main()
