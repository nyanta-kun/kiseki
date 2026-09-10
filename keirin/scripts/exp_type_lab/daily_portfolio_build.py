#!/usr/bin/env python3
"""1日単位の商品ポートフォリオ検証の台を作る（2026-09-10）。

`roi_cutoff.py` の `build()` と**同じ商品**（本番 `sell_plans_for` が選ぶ1レース1商品を
入稿ゲートまで通したもの）を作り、日次の分解に要るメタ（会場・開催日目・グレード・
race_key・板の index）を足して pickle へ保存する。

🔴 本番の日次上限は**波ごと**（morning/noon/evening）に掛かるが、ここでは日単位で近似する。
   波の情報は板に無い（`meeting_wave` は発走時刻から決まる）。§限界に明記すること。
"""
from __future__ import annotations
import pickle, sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import ctx, _plan_for, MIN_MEAN_PAYOUT, MIN_POINT_ODDS
from src.type_lab import PLANS, allocate, build_legs, mean_expected_payout, SIGNBOARD_RACE_TYPES

OUT = Path("/tmp/daily_portfolio_rows.pkl")


def run_named(x, key: str):
    plan = PLANS[key]
    pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st), mean=mean,
                sump=float(sum(prb.get(c, 0.0) for c in st)))


def build() -> list[dict]:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = z["AXIS_SUM"].astype(float)
    RP = z["A_race_point"].astype(float)
    VEN = np.array([str(v) for v in z["VENUE"]])
    KEY = np.array([str(v) for v in z["KEY"]])
    CUPG = np.array([str(v) for v in z["CUPG"]])
    DAYIDX = z["DAYIDX"].astype(int)
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 4000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            trio_ok = (run_named(x, "A_trio") is not None) if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            r = run_named(x, key)
            if not r:
                continue
            r.update(win=win, plan=key, axis=float(axs[i]), pw_ent=float(x.shape.pw_ent),
                     rp_sd=float(RP[i].std()), rtype=str(rt[i]), type=str(tp[i]),
                     i=i, venue=str(VEN[i]), race_key=str(KEY[i]),
                     cupg=str(CUPG[i]), dayidx=int(DAYIDX[i]))
            rows.append(r)
    return rows


if __name__ == "__main__":
    rows = build()
    pickle.dump(rows, open(OUT, "wb"))
    print(f"saved {len(rows):,} -> {OUT}")


# ── 高額枠の候補（日次上限で捨てた型B/C/D のレースに置く）────────────────────
def build_highpay() -> dict:
    """`{型}_sign` / `{型}_big` を型B/C/D の全レースで組んでおく（置けたものだけ）。"""
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    KEY = np.array([str(v) for v in z["KEY"]])
    out: dict[str, dict[str, dict]] = {}
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "BCD"]
        for n, i in enumerate(idx):
            if n % 3000 == 0:
                print(f"  HP {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            for kind in ("sign", "big"):
                key = f"{tp[i]}_{kind}"
                r = run_named(x, key)
                if r:
                    r.update(win=win, plan=key, type=str(tp[i]), rtype=str(rt[i]))
                    out.setdefault(str(KEY[i]), {})[key] = r
    return out
