#!/usr/bin/env python3
"""合成オッズ×的中確率(τ) の2次元で間引く案の台を作る（2026-09-10・ユーザー依頼）。

`miss_anatomy_build.py` と同じ母集団（1レース1売り商品・入稿ゲート通過後）に
**τ（買った点のモデル確率の合計）** と **較正済み τ** を焼き足す。

τ の定義は `coverage_framework_2026_08_31.md` 2章と同じ（買い目のモデル確率の合計）。
較正は同 6章の `c(O) = exp(0.497) * O^(-0.128)`（探索窓だけで当てた固定係数）。
"""
from __future__ import annotations

import itertools
import math
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, build_legs, mean_expected_payout)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/synth_thin_rows.pkl")
CAL_A, CAL_B = math.exp(0.497), -0.128


def cal(o: float) -> float:
    return CAL_A * (float(o) ** CAL_B)


def build_one(x, key: str):
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    # τ = 買った点のモデル確率の合計
    tau = float(sum(float(prb.get(c, 0.0)) for c in st))
    # 較正 τ: 目ごとに c(O) を掛け、レース内の全候補で正規化する
    w = {c: float(prb.get(c, 0.0)) * cal(o) for c, o in pod.items() if o and o > 0}
    z = sum(w.values()) or 1.0
    tau_cal = float(sum(w.get(c, 0.0) for c in st) / z)
    # 参考: 買い目の予測オッズ（配分の重み付け前）
    pos = [float(pod[c]) for c in st]
    return dict(plan=key, trio=trio, k=len(st), inv=float(sum(st.values())),
                pay=pay, mean=float(mean), tau=tau, tau_cal=tau_cal,
                po_min=min(pos), po_med=float(np.median(pos)), legs=list(st))


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    ven = np.array([str(v) for v in z["VENUE"]])
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            trio_ok = build_one(x, "A_trio") is not None if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            r = build_one(x, key)
            if r is None:
                continue
            rows.append(dict(
                i=i, race_key=str(z["KEY"][i]), win=win, date=x.date,
                venue=ven[i], rtype=rt[i], type=tp[i],
                axis=float(z["AXIS_SUM"][i]), arare=int(z["ARARE"][i]),
                plan=r["plan"], trio=r["trio"], k=r["k"], inv=r["inv"],
                pay=r["pay"], mean=r["mean"], tau=r["tau"], tau_cal=r["tau_cal"],
                po_min=r["po_min"], po_med=r["po_med"],
                hit=r["pay"] > 0, shown=r["pay"] >= r["inv"]))
    with OUT.open("wb") as f:
        pickle.dump(rows, f)
    print(f"wrote {len(rows):,} rows -> {OUT}")


if __name__ == "__main__":
    main()
