#!/usr/bin/env python3
"""軸1が薄い層での 3者対決（① 現行 / ② 軸差し替え / ③ bust）を本番経路で組む。

🔴 本番関数のみ: `race_shape` / `sell_plans_for` / `build_with_gate_fallback` /
   `build_legs` / `allocate` / `mean_expected_payout` / `passes_axis_gate`。
   ②③ は **`pred_odds` 辞書を絞る**（プール制限）か **`replace(shape, order=...)`**
   （軸の再指名）で表現する。買い方（帯・点数・配分・τ適応・ゲート代替）は触らない。

腕:
  A群 現行プランのまま プールを絞る（三連単プランのみ／三連複は set 版）
    all         制限なし（=①現行。base と同一構成）
    hd_not_a1   1着 ≠ 軸1（軸1は2・3着でなら買う）
    hd_a2       1着 = 軸2 に差し替え
    hd_a23      1着 ∈ {軸2, 軸3}
    hd_mkt      1着 = 予測オッズ由来の1着シェア最大（=市場最人気）
    hd_pw       1着 = pw 最上位
    bust        軸1 をどこにも買わない（=③）
    bust_pw     pw 最上位をどこにも買わない
    bust_mkt    市場最人気をどこにも買わない
  B群 軸の再指名（`replace(shape, order=...)`）— プランの構造側が軸を使う場合だけ効く
    sw_a2 / sw_pw / sw_mkt / sw_hon / sw_a2dis（軸1≠市場最人気のときだけ軸2へ）
  C群 signboard @ T（計画払戻を揃えた一撃商品の枠）× プール
    {all,bust,bust_pw,hd_not_a1,hd_a2,hd_mkt}@{20k,30k,150k,400k}

出力: /tmp/11_rows.pkl
"""
from __future__ import annotations

import importlib.util
import itertools
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from src.type_lab import (  # noqa: E402
    BUDGET, MIN_MEAN_PAYOUT, PLANS, Plan, SIGNBOARD_MAX_ODDS, allocate,
    build_legs, build_with_gate_fallback, mean_expected_payout, race_shape,
    sell_plans_for)
from src.strategy_wt import (  # noqa: E402
    rank_7t3_blend_probs, rank_7t3_order_swap_probs)

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
passes_axis_gate = _G.passes_axis_gate
cap_priority = _G.cap_priority

MIN_POINT_ODDS = 2.0
PERMS = C.CANON
C3 = C.CANON3
OUT = Path("/tmp/11_rows.pkl")

TARGETS = (20_000, 30_000, 150_000, 400_000)
SIGN_POOLS = ("all", "bust", "bust_pw", "hd_not_a1", "hd_a2", "hd_mkt")
POOLS = ("all", "hd_not_a1", "hd_a2", "hd_a23", "hd_mkt", "hd_pw",
         "bust", "bust_pw", "bust_mkt")
SWAPS = ("sw_a2", "sw_pw", "sw_mkt", "sw_hon", "sw_a2dis")


def _sign_plan(t: int, bust: bool = False) -> Plan:
    return Plan("_x", "?", "trifecta", "signboard", 0,
                max_odds=SIGNBOARD_MAX_ODDS, alloc="dutch", target=t, bust=bust)


_SIGN = {t: _sign_plan(t) for t in TARGETS}


def tf_filter(name: str, a1: int, a2: int, a3: int, pw1: int, mkt1: int):
    """三連単の買い目 (1着,2着,3着) を採るか。"""
    if name == "all":
        return None
    if name == "hd_not_a1":
        return lambda k: k[0] != a1
    if name == "hd_a2":
        return lambda k: k[0] == a2
    if name == "hd_a23":
        return lambda k: k[0] in (a2, a3)
    if name == "hd_mkt":
        return lambda k: k[0] == mkt1
    if name == "hd_pw":
        return lambda k: k[0] == pw1
    if name == "bust":
        return lambda k: a1 not in k
    if name == "bust_pw":
        return lambda k: pw1 not in k
    if name == "bust_mkt":
        return lambda k: mkt1 not in k
    raise KeyError(name)


def trio_filter(name: str, a1: int, pw1: int, mkt1: int):
    """三連複は順不同なので head 系は定義できない。bust 系だけ。"""
    if name == "all":
        return None
    if name == "bust":
        return lambda c: a1 not in c
    if name == "bust_pw":
        return lambda c: pw1 not in c
    if name == "bust_mkt":
        return lambda c: mkt1 not in c
    return False          # 定義できない


def promote(order: tuple[int, ...], car: int) -> tuple[int, ...]:
    """`car` を先頭へ移した並び（残りは元の順序を保つ）。"""
    return (car,) + tuple(c for c in order if c != car)


def main(limit: int | None = None):
    z = C.board()
    A = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                           "BEHIND", "DAYI", "A_prediction_mark", "PO", "PROB",
                           "WIN", "PAY", "TRIO_PO", "TRIO_ODDS", "TRIO_WIN",
                           "TRIO_PAY", "DATE", "TYPE", "RTYPE", "AXIS_SUM",
                           "GAP", "GRADE2", "CUPG2")}
    TYPE = np.array([str(v) for v in A["TYPE"]])
    RT = np.array([str(v) for v in A["RTYPE"]])
    cars = list(range(1, 8))
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if TYPE[int(i)] in "ABCDEF"]
        if limit:
            idx = idx[:limit]
        for n, i in enumerate(idx):
            if n % 1000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            p3 = {c: float(A["P3"][i][c - 1]) for c in cars}
            pw = {c: float(A["PW"][i][c - 1]) for c in cars}
            lg = {c: str(A["LG"][i][c - 1]) for c in cars}
            # 🔴 int で渡す（float だと `str(line_pos)=="1"` が外れて型ラベルがずれる）
            lp = {c: int(A["A_line_pos"][i][c - 1]) for c in cars}
            shape = race_shape(
                p3, lg, lp,
                {c: str(A["ST"][i][c - 1]) for c in cars},
                {c: float(A["A_race_point"][i][c - 1]) for c in cars},
                {c: float(A["BEHIND"][i][c - 1]) for c in cars},
                int(A["DAYI"][i]), pw)
            if shape is None or shape.type_label != TYPE[i]:
                continue
            po = A["PO"][i].astype(float)
            ok = np.isfinite(po) & (po > 0)
            if ok.sum() < 60:
                continue
            pod = {PERMS[t]: float(po[t]) for t in range(210) if ok[t]}
            inv = np.where(ok, 1.0 / np.maximum(po, 1e-9), 0.0)
            mw = np.zeros(7)
            for t, k in enumerate(PERMS):
                if ok[t]:
                    mw[k[0] - 1] += inv[t]
            mkt1 = int(np.argmax(mw) + 1)
            pw1 = max(cars, key=lambda c: pw[c])
            mk = {c: int(A["A_prediction_mark"][i][c - 1]) for c in cars}
            hons = [c for c in cars if mk[c] == 1]
            hon = hons[0] if len(hons) == 1 else 0
            a1, a2, a3 = shape.order[0], shape.order[1], shape.order[2]
            prb = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
            oprb = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
            pod3 = {frozenset(c): float(A["TRIO_PO"][i][j]) for j, c in enumerate(C3)
                    if np.isfinite(A["TRIO_PO"][i][j]) and A["TRIO_PO"][i][j] > 0}
            prb3 = {frozenset(c): sum(prb.get(p, 0.0)
                                      for p in itertools.permutations(c)) for c in C3}
            wtf = PERMS[int(A["WIN"][i])]
            paytf = float(A["PAY"][i]) / 100.0
            wt3 = frozenset(C3[int(A["TRIO_WIN"][i])])
            od3 = float(A["TRIO_PAY"][i])
            rp = np.array([float(A["A_race_point"][i][c - 1]) for c in cars])

            def payout(st, trio: bool):
                w = wt3 if trio else wtf
                if w not in st:
                    return 0.0
                return (float(st[w]) * od3 if trio
                        else float(st[w]) / 100.0 * paytf * 100.0)

            def fin(got, po_, trio):
                legs, st, used = got
                mean = mean_expected_payout(st, po_)
                gate = (mean > MIN_MEAN_PAYOUT
                        and min(float(po_[c]) for c in st) >= MIN_POINT_ODDS)
                return dict(k=len(st), inv=float(sum(st.values())),
                            pay=payout(st, trio), mean=float(mean),
                            gate=bool(gate), plan=used.key,
                            hit_in=bool((wt3 if trio else wtf) in st))

            # ── ① 現行（売る商品を1つ決める）──
            trio_ok = None
            pl = PLANS["A_trio"]
            lg2 = build_legs(shape, pl, pod3, prb3)
            if lg2:
                st2 = allocate(lg2, pod3, prb3, pl)
                trio_ok = bool(st2 and mean_expected_payout(st2, pod3) > MIN_MEAN_PAYOUT
                               and min(float(pod3[c]) for c in st2) >= MIN_POINT_ODDS)
            sp = sell_plans_for(TYPE[i], 7, RT[i], pw_ent=shape.pw_ent, trio_ok=trio_ok)
            if not sp:
                continue
            plan = sp[0]
            trio = plan.bet_type == "trio"
            po_full, pr_full = (pod3, prb3) if trio else (pod, prb)
            got = build_with_gate_fallback(shape, plan, po_full, pr_full, 7,
                                           order_probs=None if trio else oprb)
            if not got:
                continue
            base = fin(got, po_full, trio)
            base["axis_ok"] = bool(passes_axis_gate(plan.key, float(A["AXIS_SUM"][i]), 7))
            base["capp"] = float(cap_priority(plan.key, float(A["AXIS_SUM"][i]),
                                              float(np.std(rp))) or 0.0)

            arms: dict[str, dict] = {}
            inpool: dict[str, bool] = {}
            # ── A群: 現行プラン × プール制限 ──
            for pn in POOLS:
                if trio:
                    f = trio_filter(pn, a1, pw1, mkt1)
                    if f is False:
                        continue
                    p_ = po_full if f is None else {k: v for k, v in pod3.items() if f(k)}
                    inpool[pn] = bool(wt3 in p_)
                else:
                    f = tf_filter(pn, a1, a2, a3, pw1, mkt1)
                    p_ = po_full if f is None else {k: v for k, v in pod.items() if f(k)}
                    inpool[pn] = bool(wtf in p_)
                if len(p_) < 2:
                    continue
                g = build_with_gate_fallback(shape, plan, p_, pr_full, 7,
                                             order_probs=None if trio else oprb)
                if g:
                    arms[pn] = fin(g, p_, trio)
            # ── B群: 軸の再指名 ──
            for sw in SWAPS:
                tgt = {"sw_a2": a2, "sw_pw": pw1, "sw_mkt": mkt1,
                       "sw_hon": hon, "sw_a2dis": (a2 if a1 != mkt1 else a1)}[sw]
                if not tgt or tgt == a1:
                    arms[sw] = dict(base, noop=True)
                    continue
                sh2 = replace(shape, order=promote(shape.order, tgt))
                g = build_with_gate_fallback(sh2, plan, po_full, pr_full, 7,
                                             order_probs=None if trio else oprb)
                if g:
                    arms[sw] = fin(g, po_full, trio)
                    arms[sw]["noop"] = False
            # ── C群: signboard @ T × プール（三連単のみ）──
            for t in TARGETS:
                for pn in SIGN_POOLS:
                    f = tf_filter(pn, a1, a2, a3, pw1, mkt1)
                    p_ = pod if f is None else {k: v for k, v in pod.items() if f(k)}
                    if len(p_) < 2:
                        continue
                    spl = _SIGN[t]
                    legs = build_legs(shape, spl, p_, prb)
                    if not legs:
                        continue
                    st = allocate(legs, p_, prb, spl)
                    if not st:
                        continue
                    mean = mean_expected_payout(st, p_)
                    gate = (mean > MIN_MEAN_PAYOUT
                            and min(float(p_[c]) for c in st) >= MIN_POINT_ODDS)
                    arms[f"S:{pn}@{t}"] = dict(
                        k=len(st), inv=float(sum(st.values())),
                        pay=payout(st, False), mean=float(mean), gate=bool(gate),
                        plan=f"S:{pn}@{t}", hit_in=bool(wtf in st))

            rows.append(dict(
                win=win, key=int(i), date=str(A["DATE"][i]), type=TYPE[i],
                rtype=RT[i], grade=str(A["GRADE2"][i]), cupg=str(A["CUPG2"][i]),
                axis_sum=float(A["AXIS_SUM"][i]), gap=float(A["GAP"][i]),
                pw_ent=float(shape.pw_ent), rp_sd=float(np.std(rp)),
                p3_gap12=float(p3[a1] - p3[a2]), p3_gap13=float(p3[a1] - p3[a3]),
                pw_gap12=float(sorted(pw.values(), reverse=True)[0]
                               - sorted(pw.values(), reverse=True)[1]),
                pw_max=float(max(pw.values())),
                a1=a1, a2=a2, a3=a3, pw1=pw1, mkt1=mkt1, hon=hon,
                dis=bool(a1 != mkt1), trio=bool(trio),
                w1=int(wtf[0]), a1_out=bool(a1 not in wtf),
                pay_tf=paytf, base=base, arms=arms, inpool=inpool))
    OUT.write_bytes(pickle.dumps(rows))
    print(f"保存 {OUT}  {len(rows):,} 行")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
