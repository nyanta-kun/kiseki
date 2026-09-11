#!/usr/bin/env python3
"""λr（並べ替え）の**無作為対照**（2026-09-11）。

`apply_order_swap` が **同じレースで同じ点数だけ**入れ替える無作為版を 20 seed 作り、
「効いているのは並べ替えたこと自体ではなく λr の選び方だ」を確かめる。

- 台と経路は `order_swap_build.py` と同一（`apply_line_swap` 込み・τ適応込み・軸ゲート込み）。
- 各レースで実装が入れ替えた点数 `d` を数え、**無作為版もちょうど `d` 点だけ**動かす。
  候補は実装と同じ制約（同じ3車の別の並び・帯の中・まだ買っていない並び）。
- 再配分は実装と同じ `allocate(out, pred_odds, order_probs, plan)`。
  入稿ゲート（平均想定払戻 2万円・全点2.0倍）を割ったら**入れ替えない**（実装と同じ）。
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402
from src.strategy_wt import (rank_7t3_blend_probs,           # noqa: E402
                             rank_7t3_order_swap_probs)

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                    # type: ignore[union-attr]

MIN_MEAN, MIN_PO = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3
NSEED = 20

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

# `apply_order_swap` の入口を捕まえる
_CAP: dict = {}
_orig = TL.apply_order_swap


def _wrap(plan, legs, stakes, pred_odds, order_probs, min_mean_payout=TL.MIN_MEAN_PAYOUT):
    out = _orig(plan, legs, stakes, pred_odds, order_probs, min_mean_payout)
    if plan.key in TL.ORDER_SWAP_PLANS and plan.bet_type == "trifecta" and order_probs:
        _CAP.update(plan=plan, legs_in=[tuple(c) for c in legs],
                    stakes_in=dict(stakes), po=pred_odds, oprob=order_probs,
                    legs_out=[tuple(c) for c in out[0]], stakes_out=dict(out[1]))
    return out


TL.apply_order_swap = _wrap


def gate_ok(st, po):
    if not st:
        return False
    if min(float(po.get(c, 0.0) or 0.0) for c in st) < MIN_PO:
        return False
    return float(TL.mean_expected_payout(st, po)) > MIN_MEAN


def alts_of(c, lo, hi, have, po):
    out = []
    for q in itertools.permutations(sorted(set(c))):
        if q == c or q in have:
            continue
        o = po.get(q)
        if not o or float(o) <= 0 or float(o) < lo or (hi and float(o) > hi):
            continue
        out.append(q)
    return out


def random_swap(rng, plan, legs_in, stakes_in, po, oprob, d):
    """ちょうど `d` 点を無作為な別の並びへ移す。無理なら元のまま。"""
    if d <= 0:
        return stakes_in
    lo, hi = float(plan.min_odds or 0.0), float(plan.max_odds or 0.0)
    have = set(legs_in)
    out = list(legs_in)
    pos = list(rng.permutation(len(legs_in)))
    moved = 0
    for j in pos:
        if moved >= d:
            break
        a = alts_of(out[j], lo, hi, have, po)
        if not a:
            continue
        q = a[int(rng.integers(0, len(a)))]
        have.discard(out[j])
        have.add(q)
        out[j] = q
        moved += 1
    if moved == 0:
        return stakes_in
    st = TL.allocate(out, po, oprob, plan)
    if not st or len(st) != len(out):
        return stakes_in
    if float(TL.mean_expected_payout(st, po)) <= MIN_MEAN:
        return stakes_in
    return st


def main() -> None:
    for win, label in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        idx = [int(i) for i in C.select(None, win) if str(Z["TYPE"][int(i)]) in "BF"]
        rng = np.random.default_rng(20260911)
        cur, lam, alo, reo = [], [], [], []
        ctrl = [[] for _ in range(NSEED)]
        nd = set()
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
            order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
            lines = TL._lines_of(lg, lp)
            tl = str(Z["TYPE"][i])
            shape = TL.RaceShape(tl, float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
                                 float(Z["GAP"][i]),
                                 float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
                                 order, TL.win_entropy(pw), lines,
                                 TL._strongest_pair(lines, p3))
            sel = TL.sell_plans_for(tl, 7, str(Z["RTYPE"][i]), pw_ent=shape.pw_ent)
            if not sel or sel[0].key not in ("B_hit", "F_hit"):
                continue
            plan = sel[0]
            if not _G.passes_axis_gate(plan.key, shape.axis_sum, 7):
                continue
            _CAP.clear()
            got = TL.build_with_gate_fallback(shape, plan, po, pr, 7, order_probs=orp)
            if not got or not _CAP:
                continue
            legs, st, pl = got
            if not gate_ok(st, po):
                continue
            wtf, ptf = PERMS[int(Z["WIN"][i])], float(Z["PAY"][i]) / 100.0
            pay = lambda s: (float(s[wtf] / 100.0 * ptf * 100.0) if wtf in s else 0.0)
            inv = lambda s: float(sum(s.values()))
            si, so = _CAP["stakes_in"], _CAP["stakes_out"]
            d = sum(1 for a, b in zip(_CAP["legs_in"], _CAP["legs_out"]) if a != b)
            cur.append((inv(si), pay(si)))
            lam.append((inv(so), pay(so)))
            # 🔴 分解: `apply_order_swap` は **再配分に `order_probs` を使う**ので、
            #    並べ替えと配分の2つが同時に動いている。片方ずつに割る。
            def _try(lg_, pb_):
                st_ = TL.allocate(lg_, po, pb_, plan)
                if not st_ or len(st_) != len(lg_):
                    return si
                if float(TL.mean_expected_payout(st_, po)) <= MIN_MEAN:
                    return si
                return st_
            sa = _try(_CAP["legs_in"], orp)          # 配分だけ λr（並びは元のまま）
            sr = _try(_CAP["legs_out"], pr)          # 並べ替えだけ（配分は元の probs）
            alo.append((inv(sa), pay(sa)))
            reo.append((inv(sr), pay(sr)))
            for s in range(NSEED):
                sc = random_swap(rng, _CAP["plan"], _CAP["legs_in"], si,
                                 _CAP["po"], _CAP["oprob"], d)
                ctrl[s].append((inv(sc), pay(sc)))
            nd.add(str(Z["DATE"][i]))
        shown = lambda rs: sum(1 for v, p in rs if p > v) / len(rs) * 100
        vals = [shown(c) for c in ctrl]
        wins = sum(1 for v in vals if shown(lam) > v)
        print(f"\n=== {label}  B_hit+F_hit n={len(cur):,}（営業日 {len(nd)}）===")
        print(f"  ①現行(並べ替えなし) 表示的中 {shown(cur):.2f}%")
        print(f"  ②λr                表示的中 {shown(lam):.2f}%  Δ {shown(lam)-shown(cur):+.2f}pt")
        print(f"  ├ 配分だけ λr       表示的中 {shown(alo):.2f}%  Δ {shown(alo)-shown(cur):+.2f}pt")
        print(f"  └ 並べ替えだけ      表示的中 {shown(reo):.2f}%  Δ {shown(reo)-shown(cur):+.2f}pt")
        print(f"  無作為対照 {NSEED}seed 中央 {np.median(vals):.2f}% "
              f"（範囲 {min(vals):.2f}〜{max(vals):.2f}・平均 {np.mean(vals):.2f}）"
              f"  Δ対照中央 {shown(lam)-np.median(vals):+.2f}pt")
        print(f"  λr の勝ち {wins}/{NSEED}"
              f"   対照が現行に勝った seed {sum(1 for v in vals if v > shown(cur))}/{NSEED}")


if __name__ == "__main__":
    main()
