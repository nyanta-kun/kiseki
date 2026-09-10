#!/usr/bin/env python3
"""ライン構造で「順序」を当て直せるかの台（2026-09-10・ユーザー依頼）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

順序を決めているのは **2箇所**で、どちらも既にライン情報を使っている:

1. `src.strategy_wt.rank_7t3_blend_probs` — 位置別合成 PL に
   `λ^[y は x の直後] × μ^[z は y の直後]`（λ=2.0 / μ=1.5）。
   🔴 `_line_next(a,b)` は `line_pos[b] == line_pos[a]+1` の**一方向**なので、
      「番手 → 先頭」（＝番手が先頭を差す並び）には**ボーナスが掛からない**。
2. `src.type_lab.apply_line_swap`（2026-09-05）— `B/C/E/F_hit` の確率下位 m 点を
   「同一ライン3車の目」へ差し替える。先頭×番手の入れ替え2通りを優先。

板の `PROB` はボーナス**前**なので使わない。本番と同じ `rank_7t3_blend_probs` で組み直す。

🔴 `race_shape()` には `int()` した `line_pos` を渡す（float32 のままだと
   `str(line_pos)=="1"` が外れ、`lines` が隊列順でなく車番順になる）。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, apply_line_swap, build_legs,
    mean_expected_payout, race_shape, win_entropy)
from src.strategy_wt import rank_7t3_blend_probs  # noqa: E402

PERMS, C3 = C.CANON, C.CANON3
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/lo/rows.pkl")
CARS = list(range(1, 8))


def load_rates() -> dict:
    d: dict[str, dict[int, tuple]] = defaultdict(dict)
    with open("/tmp/lo/rates.csv") as f:
        for ln in f:
            k, fn, a, b, c = ln.rstrip("\n").split(",")
            d[k][int(fn)] = (float(a), float(b), float(c))
    return d


import importlib.util  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN


def _plan_for(tp: str, rtype: str, pw_ent: float, trio_ok: bool) -> str:
    """`sell_plans_for` そのもの（7車）。"""
    from src.type_lab import sell_plans_for
    keys = sell_plans_for(tp, 7, rtype, pw_ent=pw_ent, trio_ok=trio_ok)
    return keys[0].key if keys else ""


class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date", "lg", "lp", "rp", "p3", "pw")


def build_ctx(a, i: int) -> Ctx | None:
    p3 = {c: float(a["P3"][i][c - 1]) for c in CARS}
    pw = {c: float(a["PW"][i][c - 1]) for c in CARS}
    lg = {c: str(a["LG"][i][c - 1]) for c in CARS}
    lp_raw = a["A_line_pos"][i]
    lp = {c: (int(lp_raw[c - 1]) if np.isfinite(lp_raw[c - 1]) else 0) for c in CARS}
    st = {c: str(a["ST"][i][c - 1]) for c in CARS}
    rp = {c: float(a["A_race_point"][i][c - 1]) for c in CARS}
    bh = {c: float(a["BEHIND"][i][c - 1]) for c in CARS}
    shape = race_shape(p3, lg, lp, st, rp, bh, int(a["DAYI"][i]), win_probs=pw)
    if shape is None:
        return None
    pr = rank_7t3_blend_probs(CARS, pw, p3, line_group=lg, line_pos=lp)
    po = {PERMS[t]: float(a["PO"][i][t]) for t in range(210)
          if np.isfinite(a["PO"][i][t]) and a["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    x = Ctx()
    x.shape, x.po_tf, x.pr_tf = shape, po, pr
    x.po_t3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
               for c in C3}
    x.win_tf = PERMS[int(a["WIN"][i])]
    x.pay_tf = float(a["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(a["TRIO_WIN"][i])])
    x.odds_t3 = float(a["TRIO_PAY"][i])
    x.date = str(a["DATE"][i])
    x.lg, x.lp, x.rp, x.p3, x.pw = lg, lp, rp, p3, pw
    return x


def build_one(x: Ctx, key: str, swap: bool = True):
    """本番と同じ順で組む: build_legs → allocate → apply_line_swap → 入稿ゲート。"""
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if swap and not trio:
        legs, st = apply_line_swap(x.shape, plan, legs, st, pod, prb)
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(plan=key, trio=trio, k=len(st), inv=float(sum(st.values())),
                pay=pay, mean=float(mean), legs=list(st), stakes=dict(st))


def line_of(lg, c):
    g = lg.get(c)
    return None if g in (None, "", "0") else g


def main() -> None:
    z = C.board()
    a = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                           "BEHIND", "DAYI", "PO", "TRIO_PO", "TRIO_ODDS",
                           "TRIO_WIN", "TRIO_PAY", "WIN", "PAY", "DATE", "TYPE",
                           "RTYPE", "VENUE", "KEY", "AXIS_SUM", "ARARE", "GAP",
                           "AGREE", "A_line_size", "A_n_lines")}
    rates = load_rates()
    tp = np.array([str(v) for v in a["TYPE"]])
    rt = np.array([str(v) for v in a["RTYPE"]])
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows, mism = [], 0
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = build_ctx(a, i)
            if x is None:
                continue
            if x.shape.type_label != tp[i]:
                mism += 1
                continue
            trio_ok = build_one(x, "A_trio") is not None if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok)
            if not key:
                continue
            # 🔴 軸信頼ゲート（第3層 #2・A/D/E/F_hit の4プランのみ）
            if float(a["AXIS_SUM"][i]) < AXIS_GATE_MIN.get(key, 0.0):
                continue
            r = build_one(x, key)
            if r is None:
                continue
            rk = str(a["KEY"][i])
            rr = rates.get(rk, {})
            fin = x.win_tf
            # ── ライン構造 ────────────────────────────────────────────
            lines = x.shape.lines                     # 隊列順・2車以上
            lmap = {c: gi for gi, ln in enumerate(lines) for c in ln}
            posin = {c: j for ln in lines for j, c in enumerate(ln)}
            g1, g2, g3 = (lmap.get(fin[0]), lmap.get(fin[1]), lmap.get(fin[2]))
            same12 = g1 is not None and g1 == g2
            same123 = same12 and g2 == g3
            # 隊列順どおりか（同ラインの範囲で）
            ord12 = (posin[fin[0]] < posin[fin[1]]) if same12 else None
            ord123 = (posin[fin[0]] < posin[fin[1]] < posin[fin[2]]) if same123 else None
            legs_set = set(r["legs"])
            if r["trio"]:
                in_legs = x.win_t3 in legs_set
                set_hit = in_legs
            else:
                in_legs = fin in legs_set
                set_hit = frozenset(fin) in {frozenset(c) for c in legs_set}
            rows.append(dict(
                i=i, race_key=rk, win=win, date=x.date, type=tp[i], rtype=rt[i],
                venue=str(a["VENUE"][i]), plan=r["plan"], trio=r["trio"],
                k=r["k"], inv=r["inv"], pay=r["pay"], mean=r["mean"],
                hit=r["pay"] > 0, shown=r["pay"] >= r["inv"],
                in_legs=in_legs, set_hit=set_hit,
                legs=r["legs"], stakes=r["stakes"],
                fin=fin, axis=float(a["AXIS_SUM"][i]), arare=int(a["ARARE"][i]),
                order=tuple(x.shape.order), lines=lines,
                same12=same12, same123=same123, ord12=ord12, ord123=ord123,
                lg=dict(x.lg), lp=dict(x.lp), rp=dict(x.rp),
                p3=dict(x.p3), pw=dict(x.pw),
                rates={c: rr.get(c, (-1.0, -1.0, -1.0)) for c in CARS},
                po_win=float(x.po_tf.get(fin, np.nan)),
                pay_tf=float(x.pay_tf),
            ))
    print(f"型ラベル不一致で除外 {mism:,} / 行 {len(rows):,}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pickle.dump(rows, OUT.open("wb"))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
