#!/usr/bin/env python3
"""不来確率での「相手削り」を現行の足切り（p3>=0.15）と同じ形で比べる（2026-08-25）。

`exp_trio_exclusion_model.py` は**固定点数**で並べ替えの質だけを見た。
本スクリプトは提案の native な形＝**閾値で可変点数**にして、
現行 `rank_7c_select_legs`（周辺 p3 の絶対足切り）と同じ土俵で比べる。

  現行 : p3(相手)               >= θ
  MJ   : P(三者同時3着内)        >= θ
  ME   : P(相手が3着外 | ◎3着内) <= θ
  ME2  : P(相手が3着外 | 二軸3着内) <= θ

**平均点数を揃えて**比較する（点数が違えば1点あたりの賭け金が違い、
的中率も払戻も自動的に動くため、揃えないと何を測ったか分からなくなる）。
同じ平均点数の**固定点数**も並べ、「可変にすること自体」に価値があるかを見る。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_exclusion_model import build  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    day_ci, fit, load_any, load_boards, load_entries,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEG_P3_MIN, RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN, unit_stake,
)

PAYOUT_RATE = 0.7485


def agg(rows):
    n = len(rows)
    if not n:
        return None
    bet = sum(r[4] for r in rows); pay = sum(r[3] for r in rows)
    pl = sorted(r[3] for r in rows if r[2])
    days = len({r[0] for r in rows})
    return dict(n=n, legs=float(np.mean([r[1] for r in rows])),
                hit=sum(1 for r in rows if r[2]) / n,
                disp=sum(1 for r in rows if r[3] > r[4]) / n,
                roi=pay / max(bet, 1), med=(np.median(pl) if pl else 0.0),
                big2=sum(1 for r in rows if r[3] >= 20000) / max(days, 1),
                big5=sum(1 for r in rows if r[3] >= 50000) / max(days, 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio7_cache_wf_train.jsonl")
    ap.add_argument("--test", default="data/exp/trio7_cache_wf_test.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    ap.add_argument("--pop", default="7c", choices=["all", "7c"])
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    print(f"学習 {len(tr):,}R / 検定 {len(te):,}R  母集団={args.pop}"
          f"  ({min(r['date'] for r in te)}〜{max(r['date'] for r in te)})")

    ent_tr = load_entries([r["key"] for r in tr])
    ent_te = load_entries([r["key"] for r in te])
    fin_tr = _load_finishes([r["key"] for r in tr])
    fin_te = _load_finishes([r["key"] for r in te])
    board = load_boards([r["key"] for r in te])

    Xtr, ytr, yc_tr, a1_tr, ph_tr, _ = build(tr, ent_tr, fin_tr)
    Xte, yte, yc_te, a1_te, ph_te, mte = build(te, ent_te, fin_te)

    mJ = fit(Xtr, ytr, args.rounds); pJ = mJ.predict(Xte)
    s = a1_tr == 1
    mE = fit(Xtr[s], 1 - yc_tr[s], args.rounds); pE = mE.predict(Xte)
    s2 = ph_tr == 1
    mE2 = fit(Xtr[s2], 1 - yc_tr[s2], args.rounds); pE2 = mE2.predict(Xte)

    by = defaultdict(list); info = {}
    for (key, date, a1, a2, c, rk, p3c, asum), j, e1, e2, t in zip(
            mte, pJ, pE, pE2, yte):
        by[key].append(dict(c=c, p3=p3c, t=int(t),
                            P3=p3c, MJ=float(j), ME=1 - float(e1),
                            ME2=1 - float(e2)))
        info[key] = (date, a1, a2, asum)

    races = []
    for key, v in by.items():
        if len(v) != 5:
            continue
        bd = board.get(key)
        if not bd or any(frozenset((info[key][1], info[key][2], x["c"])) not in bd
                         for x in v):
            continue
        date, a1, a2, asum = info[key]
        prod = [x for x in v if x["p3"] >= RANK_7C_LEG_P3_MIN]
        if args.pop == "7c" and (asum < RANK_7C_P3_SUM_MIN
                                 or len(prod) < RANK_7C_LEGS_MIN):
            continue
        races.append((key, date, a1, a2, v, bd))
    print(f"評価対象 {len(races):,}R\n")

    def buy(legs, a1, a2, bd, date):
        k = max(len(legs), 1)
        st = unit_stake(k)
        pay = sum(int(bd[frozenset((a1, a2, x["c"]))] * 100) * st // 100
                  for x in legs if x["t"])
        return (date, len(legs), any(x["t"] for x in legs), pay, st * k)

    # ── 現行（p3>=0.15）を基準に置く ──
    ref = [buy([x for x in v if x["p3"] >= RANK_7C_LEG_P3_MIN] or
               sorted(v, key=lambda z: -z["p3"])[:1], a1, a2, bd, d)
           for _, d, a1, a2, v, bd in races]
    r0 = agg(ref)
    print(f"〈現行 p3>={RANK_7C_LEG_P3_MIN}〉平均{r0['legs']:.2f}点 "
          f"的中{r0['hit']:.2%} 表示的中{r0['disp']:.2%} ROI{r0['roi']:.1%} "
          f"払戻中央{r0['med']:,.0f}円 2万+{r0['big2']:.2f}/日 5万+{r0['big5']:.2f}/日")

    print("\n【閾値で相手を削る（可変点数）】最低1点は残す")
    print(f"{'腕':>5}{'閾値':>8}{'平均点':>7}{'的中%':>8}{'表示的中%':>10}"
          f"{'ROI':>8}{'ROI下限':>9}{'払戻中央':>10}{'2万+/日':>9}{'5万+/日':>9}")
    curves = {}
    for arm, ths in (("P3", [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]),
                     ("MJ", [0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20]),
                     ("ME", [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]),
                     ("ME2", [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60])):
        pts = []
        for th in ths:
            rows = []
            for _, d, a1, a2, v, bd in races:
                legs = [x for x in v if x[arm] >= th]
                if not legs:
                    legs = sorted(v, key=lambda z: -z[arm])[:1]
                rows.append(buy(legs, a1, a2, bd, d))
            a = agg(rows)
            byd_a = defaultdict(lambda: [0.0, 0.0]); byd_r = defaultdict(lambda: [0.0, 0.0])
            for (dd, _, _, p, b), (_, _, _, p2, b2) in zip(rows, ref):
                byd_a[dd][0] += b; byd_a[dd][1] += p
                byd_r[dd][0] += b2; byd_r[dd][1] += p2
            dd_ = [(byd_a[k2][0], byd_r[k2][1], byd_a[k2][1]) for k2 in byd_a]
            _, _, lo = day_ci(dd_)
            mk = " 🟢" if lo > PAYOUT_RATE else ""
            print(f"{arm:>5}{th:>8.2f}{a['legs']:>7.2f}{a['hit']:>8.2%}"
                  f"{a['disp']:>10.2%}{a['roi']:>8.1%}{lo:>9.1%}{a['med']:>10,.0f}"
                  f"{a['big2']:>9.2f}{a['big5']:>9.2f}{mk}")
            pts.append((a['legs'], a))
        curves[arm] = pts
        print()

    # ── 固定点数（可変にする価値があるか）──
    print("【固定点数（比較用・並べ替えは各腕）】")
    for arm in ("P3", "MJ", "ME2"):
        for k in (1, 2, 3, 4):
            rows = [buy(sorted(v, key=lambda z: (-z[arm], z["c"]))[:k],
                        a1, a2, bd, d) for _, d, a1, a2, v, bd in races]
            a = agg(rows)
            print(f"{arm:>5}{('k=' + str(k)):>8}{a['legs']:>7.2f}{a['hit']:>8.2%}"
                  f"{a['disp']:>10.2%}{a['roi']:>8.1%}{'':>9}{a['med']:>10,.0f}"
                  f"{a['big2']:>9.2f}{a['big5']:>9.2f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
