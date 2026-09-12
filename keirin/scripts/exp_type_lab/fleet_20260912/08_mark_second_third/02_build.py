#!/usr/bin/env python3
"""「1着を WT◎○以外にして ◎○を2・3着へ回す」買い方を本番経路で組み、結果を焼く。

🔴 本番の関数だけを使う（`race_shape` / `build_legs` / `allocate` /
   `mean_expected_payout` / `sell_plans_for` / `passes_axis_gate`）。
   候補プールの制限は **`pred_odds` の辞書を絞って渡す**ことで表現する
   （`build_legs` の `signboard` 分岐は `pred_odds.items()` を走るので、
   絞った辞書を渡せば同じコードのままプールだけが変わる）。

腕は「計画払戻 T」×「候補プール」の格子。T は `signboard` の `target`
（Σ(1/予測オッズ) <= 予算/T）。配分はダッチ固定・上限600倍も本番と同じ。

  プール          1着                  2・3着
  all             制限なし（=現行 *_sign）
  bust            軸1を1点も買わない（=現行 *_big）
  M               ∉{◎,○}              {◎,○} の**両方**
  N               ∉{◎,○}              {◎,○} の**少なくとも1車**
  N_hon           ∉{◎,○}              ◎ を必ず含む
  N_pw2           pw上位の非◎○ 2車    {◎,○} の少なくとも1車
  Mall            ∉{◎,○}              制限なし（対照: 1着だけ替えた形）

出力: /tmp/08_mst_rows.pkl
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

MIN_POINT_ODDS = 2.0
PERMS = C.CANON
C3 = C.CANON3
C3IDX = C.C3IDX
OUT = Path("/tmp/08_mst_rows.pkl")

TARGETS = (20_000, 50_000, 100_000, 150_000, 400_000)
CAPS = (0, 4, 8, 12)          # 0 = 上限なし
POOLS = ("all", "bust", "M", "N", "N_hon", "N_pw2", "Mall")
#: 重ね買い（1商品の中で下帯8割＋上帯2割）で使うプールと点数。
#: 重ね買いのプールと点数。`all`/`bust` は**対照**（制限なしで同じ点数だけ足す）。
OVERLAY = (("M", 2), ("M", 4), ("N", 2), ("N", 4),
           ("all", 2), ("all", 4), ("bust", 4))
#: 重ね買いの無作為対照の seed 数（未購入の目から同数を無作為に選ぶ）。
OV_SEEDS = 20


def _sign_plan(t: int) -> Plan:
    return Plan("_x", "?", "trifecta", "signboard", 0,
                max_odds=SIGNBOARD_MAX_ODDS, alloc="dutch", target=t)


_SIGN = {t: _sign_plan(t) for t in TARGETS}
_SIGN_BUST = {t: replace(_sign_plan(t), bust=True) for t in TARGETS}


def pool_pred(name: str, hon: int, tai: int, a1: int, pw2: tuple[int, ...]):
    """買い目 (1着,2着,3着) を採るか。"""
    ho = {hon, tai}
    if name == "M":
        return lambda k: k[0] not in ho and ho <= {k[1], k[2]}
    if name == "N":
        return lambda k: k[0] not in ho and bool(ho & {k[1], k[2]})
    if name == "N_hon":
        return lambda k: k[0] not in ho and hon in (k[1], k[2])
    if name == "N_pw2":
        return lambda k: k[0] in pw2 and bool(ho & {k[1], k[2]})
    if name == "Mall":
        return lambda k: k[0] not in ho
    return None


def _fin(legs, pod, prb, plan):
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    return st, mean


def run_sign(shape, pod, prb, t: int, bust: bool = False, cap: int = 0):
    """`signboard`（計画払戻 T・ダッチ）で組んで入稿ゲートを通す。

    cap>0 なら**確率上位 cap 点で打ち切る**（signboard は確率降順に積むので
    前方を切り出すだけ。切ると Σ が小さくなるので計画払戻は上がる）。
    """
    plan = (_SIGN_BUST if bust else _SIGN)[t]
    legs = build_legs(shape, plan, pod, prb)
    if not legs:
        return None
    if cap:
        legs = list(legs)[:cap]
    return _fin(legs, pod, prb, plan)


def main(limit: int | None = None):
    z = C.board()
    A = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                           "BEHIND", "DAYI", "A_prediction_mark", "PO", "PROB",
                           "WIN", "PAY", "TRIO_PO", "TRIO_ODDS", "TRIO_WIN",
                           "TRIO_PAY", "DATE", "TYPE", "RTYPE", "AXIS_SUM")}
    TYPE = np.array([str(v) for v in A["TYPE"]])
    RT = np.array([str(v) for v in A["RTYPE"]])
    cars = list(range(1, 8))
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if TYPE[int(i)] in "ABCDEF"]
        if limit:
            idx = idx[:limit]
        for n, i in enumerate(idx):
            if n % 2000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            mk = {c: int(A["A_prediction_mark"][i][c - 1]) for c in cars}
            hons = [c for c in cars if mk[c] == 1]
            tais = [c for c in cars if mk[c] == 2]
            if len(hons) != 1 or len(tais) != 1:
                continue
            hon, tai = hons[0], tais[0]
            p3 = {c: float(A["P3"][i][c - 1]) for c in cars}
            pw = {c: float(A["PW"][i][c - 1]) for c in cars}
            lg = {c: str(A["LG"][i][c - 1]) for c in cars}
            # 🔴 `line_pos` は **int** で渡す。`race_shape` は `str(line_pos[c]) == "1"`
            #    で先頭を探すので、numpy float を渡すと "1.0" になり lead が
            #    永久に None になる（型ラベルが 46% 食い違う。実測で踏んだ）。
            lp = {c: int(A["A_line_pos"][i][c - 1]) for c in cars}
            shape = race_shape(
                p3, lg, lp,
                {c: str(A["ST"][i][c - 1]) for c in cars},
                {c: float(A["A_race_point"][i][c - 1]) for c in cars},
                {c: float(A["BEHIND"][i][c - 1]) for c in cars},
                int(A["DAYI"][i]), pw)
            if shape is None or shape.type_label != TYPE[i]:
                continue
            pod = {PERMS[t]: float(A["PO"][i][t]) for t in range(210)
                   if np.isfinite(A["PO"][i][t]) and A["PO"][i][t] > 0}
            if len(pod) < 60:
                continue
            prb = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
            oprb = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg,
                                             line_pos=lp)
            pod3 = {frozenset(c): float(A["TRIO_PO"][i][j]) for j, c in enumerate(C3)
                    if np.isfinite(A["TRIO_PO"][i][j]) and A["TRIO_PO"][i][j] > 0}
            prb3 = {frozenset(c): sum(prb.get(p, 0.0)
                                      for p in itertools.permutations(c))
                    for c in C3}
            wtf = PERMS[int(A["WIN"][i])]
            paytf = float(A["PAY"][i]) / 100.0          # 100円あたり倍率
            wt3 = frozenset(C3[int(A["TRIO_WIN"][i])])
            od3 = float(A["TRIO_PAY"][i])
            a1 = shape.order[0]
            pw2 = tuple(sorted((c for c in cars if c not in (hon, tai)),
                               key=lambda c: -pw[c])[:2])

            def payout(st, trio: bool):
                w = wt3 if trio else wtf
                if w not in st:
                    return 0.0
                return (float(st[w]) * od3 if trio
                        else float(st[w]) / 100.0 * paytf * 100.0)

            # ── 現行ラインナップ（機会費用の基準）──
            base = None
            trio_ok = None
            pl = PLANS["A_trio"]
            lg2 = build_legs(shape, pl, pod3, prb3)
            if lg2:
                st2 = allocate(lg2, pod3, prb3, pl)
                trio_ok = bool(st2 and mean_expected_payout(st2, pod3) > MIN_MEAN_PAYOUT
                               and min(float(pod3[c]) for c in st2) >= MIN_POINT_ODDS)
            sp = sell_plans_for(TYPE[i], 7, RT[i], pw_ent=shape.pw_ent,
                                trio_ok=trio_ok)
            if sp:
                plan = sp[0]
                trio = plan.bet_type == "trio"
                po_, pr_ = (pod3, prb3) if trio else (pod, prb)
                got = build_with_gate_fallback(
                    shape, plan, po_, pr_, 7,
                    order_probs=None if trio else oprb)
                if got:
                    legs, st, used = got
                    mean = mean_expected_payout(st, po_)
                    gate = (mean > MIN_MEAN_PAYOUT
                            and min(float(po_[c]) for c in st) >= MIN_POINT_ODDS)
                    axg = passes_axis_gate(used.key, float(A["AXIS_SUM"][i]), 7)
                    base = dict(plan=used.key, k=len(st), inv=float(sum(st.values())),
                                pay=payout(st, trio), mean=float(mean),
                                gate=bool(gate), axis_ok=bool(axg),
                                trio=bool(trio), legs=list(legs),
                                hit_in=bool((wt3 if trio else wtf) in st))

            # ── 腕 ──
            arms = {}
            pools = {}
            for pn in POOLS:
                if pn == "all":
                    p_ = pod
                elif pn == "bust":
                    # 🔴 軸1 を1点も含まない目だけ（本番 `_big` の `bust=True` と同値）。
                    p_ = {k: v for k, v in pod.items() if a1 not in k}
                else:
                    f = pool_pred(pn, hon, tai, a1, pw2)
                    p_ = {k: v for k, v in pod.items() if f(k)}
                    if not p_:
                        continue
                pools[pn] = p_
                for t in TARGETS:
                    for cap in CAPS:
                        r = run_sign(shape, p_, prb, t, cap=cap)
                        if r is None:
                            continue
                        st, mean = r
                        arms[f"{pn}@{t}k{cap}"] = dict(
                            k=len(st), inv=float(sum(st.values())),
                            pay=payout(st, False), mean=float(mean))

            # ── (d) 固定構成: ◎を1着に置く形 / ◎を2着に置く形 ──
            rest_o = [c for c in shape.order if c not in (hon, tai)]
            ana3 = [c for c in cars if mk[c] == 3]
            seconds = [tai] + (ana3[:1] if ana3 else [])
            fixed = {
                # F_pay の◎版（1着=◎固定・2着∈{○,△}・3着流し2車）
                "d_hon1st": [(hon, s2, c) for s2 in seconds
                             for c in rest_o[:2] if c != s2],
                # ◎を2着へ回す素直な形（1着=pw上位の非◎○2車・2着=◎・3着流し3車）
                "d_hon2nd": [(x, hon, c) for x in pw2
                             for c in [y for y in cars
                                       if y not in (x, hon)][:3]],
                # ◎○を2・3着へ（1着=pw上位の非◎○2車・2・3着=◎○の両並び）
                "d_both": [(x, a, b) for x in pw2
                           for a, b in ((hon, tai), (tai, hon))],
            }
            for nm, legs in fixed.items():
                legs = [c for c in legs if len(set(c)) == 3 and c in pod]
                if len(legs) < 2:
                    continue
                for al, pl in (("d", _SIGN[20_000]),
                               ("c", replace(_SIGN[20_000], alloc="conf",
                                             floor_mult=2.0))):
                    r = _fin(legs, pod, prb, pl)
                    if r:
                        st, mean = r
                        arms[f"{nm}_{al}"] = dict(
                            k=len(st), inv=float(sum(st.values())),
                            pay=payout(st, False), mean=float(mean))

            # ── 重ね買い（下帯8割 + 上帯2割）──
            ov = {}
            if base is not None and base["gate"] and not base["trio"]:
                blegs, bplan = base["legs"], PLANS.get(base["plan"])
                lo = allocate(blegs, pod, prb, bplan, budget=8_000)
                if lo:
                    for pn, m in OVERLAY:
                        p_ = pools.get(pn)
                        if not p_:
                            continue
                        cand = sorted((k for k in p_ if k not in lo),
                                      key=lambda k: -prb.get(k, 0.0))[:m]
                        up = allocate(cand, p_, prb, _SIGN[20_000],
                                      budget=2_000) if cand else None
                        if not up:
                            continue
                        st = dict(lo)
                        for k, v in up.items():
                            st[k] = st.get(k, 0) + v
                        ov[f"{pn}x{m}"] = dict(
                            k=len(st), inv=float(sum(st.values())),
                            pay=payout(st, False),
                            mean=float(base["mean"]))
                    # 🔴 無作為対照: 未購入の目から同数を無作為に選ぶ。
                    #    「N の制限が効いている」のか「4点足しただけ」なのかを分ける。
                    unb = [k for k in pod if k not in lo]
                    for sd in range(OV_SEEDS):
                        rng = np.random.default_rng(sd * 1000 + i)
                        for m in (2, 4):
                            if len(unb) < m:
                                continue
                            pick = [unb[j] for j in
                                    rng.choice(len(unb), m, replace=False)]
                            up = allocate(pick, pod, prb, _SIGN[20_000],
                                          budget=2_000)
                            if not up:
                                continue
                            st = dict(lo)
                            for k, v in up.items():
                                st[k] = st.get(k, 0) + v
                            ov[f"rnd{m}s{sd}"] = dict(
                                k=len(st), inv=float(sum(st.values())),
                                pay=payout(st, False), mean=float(base["mean"]))
            rows.append(dict(
                win=win, date=str(A["DATE"][i]), key=int(i), type=TYPE[i],
                rtype=RT[i], axis_sum=float(A["AXIS_SUM"][i]),
                pw_ent=float(shape.pw_ent), hon=hon, tai=tai, a1=a1,
                hon_is_a1=bool(hon == a1),
                first_out=bool(wtf[0] not in (hon, tai)),
                n23=len({hon, tai} & {wtf[1], wtf[2]}),
                pay_tf=paytf, base=base, arms=arms, ov=ov,
                # 決着の目が各プールに含まれるか（＝そのプールの的中上限）
                inpool={pn: bool(wtf in p_) for pn, p_ in pools.items()},
                poolsz={pn: len(p_) for pn, p_ in pools.items()}))
    OUT.write_bytes(pickle.dumps(rows))
    print(f"保存 {OUT}  {len(rows):,} 行")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
