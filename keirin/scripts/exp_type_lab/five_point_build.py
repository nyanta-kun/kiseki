#!/usr/bin/env python3
"""三連単を少点数（5点前後）にできるか — 腕ごとの台を作る（2026-09-10）。

## 先に本番を読む

売っている三連単は**すべて `structure='prob_top'`・`alloc='conf'`（floor_mult=2.0）**で、
違うのは**点数と帯（`min_odds`）だけ**:

    A_hit  3点 帯なし / B_hit  8点 帯なし(Σ<1/3) / C_hit 12点 帯15倍+
    E_hit 14点 帯30倍+ / F_hit 12点 帯5倍+          （D_hit と A_trio は三連複）

＝ **「5点で当てられるか」は「点数のダイヤルをどこに置くか」と同じ問題**。
ここでは全型に同じ点数を当てる腕を作り、現行（型ごとに 3〜14点）と比べる。

入稿ゲート（平均想定払戻 > 2万円・全点の予測 >= 2.0倍）は全腕に当てる。
"""
from __future__ import annotations

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
    PLANS, Plan, SIGNBOARD_RACE_TYPES, allocate, build_legs, mean_expected_payout)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/five_point_rows.pkl")
#: 型ごとの現行の帯（`PLANS` から。D は三連複なので帯なし）
BAND = {"A": 0.0, "B": 0.0, "C": 15.0, "D": 0.0, "E": 30.0, "F": 5.0}


def run(x, plan: Plan):
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    inv = float(sum(st.values()))
    return dict(k=len(st), inv=inv, pay=pay, mean=float(mean), gate=gate)


def arm_plans(tl: str) -> dict[str, Plan]:
    """この型で試す腕（全部 `prob_top` の三連単・配分は本番と同じ conf/floor2.0）。"""
    out = {}
    for k in (3, 4, 5, 6, 8, 12):
        out[f"k{k}_noband"] = Plan(f"_k{k}", tl, "trifecta", "prob_top", 0,
                                   max_legs=k, alloc="conf", floor_mult=2.0)
        b = BAND[tl]
        out[f"k{k}_band"] = Plan(f"_k{k}b", tl, "trifecta", "prob_top", 0,
                                 min_odds=b, max_legs=k, alloc="conf", floor_mult=2.0)
    return out


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
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
            trio_ok = run(x, PLANS["A_trio"]) is not None if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            cur = run(x, PLANS[key])
            arms = {"cur": cur}
            for nm, pl in arm_plans(tp[i]).items():
                arms[nm] = run(x, pl)
            pr = x.pr_tf
            order = sorted(pr, key=lambda c: -pr[c])
            cum = np.cumsum([pr[c] for c in order])
            pos = {c: j for j, c in enumerate(order)}
            rows.append(dict(
                i=i, win=win, date=x.date, type=tp[i], rtype=rt[i], plan=key,
                axis=float(z["AXIS_SUM"][i]), gap=float(z["GAP"][i]),
                pw_ent=float(x.shape.pw_ent), agree=bool(z["AGREE"][i]),
                rank_prob=pos.get(x.win_tf, 999),
                sp1=float(cum[0]), sp3=float(cum[2]), sp5=float(cum[4]),
                sp8=float(cum[7]), sp12=float(cum[11]),
                arms=arms))
    print(f"作った行 {len(rows):,}")
    pickle.dump(rows, OUT.open("wb"))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
