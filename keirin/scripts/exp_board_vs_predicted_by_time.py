#!/usr/bin/env python3
"""配分に使う信号: 予測オッズ vs その時刻の実板（発走までの近さ別）・2026-08-19。

## 仮説

`landing_weights` は**予測オッズがあれば最優先で単独採用**する。根拠は
「朝の板 logMAE 0.331 / ±2倍 59.3% に対し 予測 0.137 / 91.5%」（`odds_prediction.py`）。

ただしこれは**朝の板**との比較。入稿は波に分かれており（朝7:00 / 昼13:00 / 夕18:00）、
昼・夕の開催では入稿時点で板がかなり育っている（20時発走の未確定率は
朝8時 63.4% → 18:00 で 10.8%）。**育った板なら予測より良いのではないか。**

もしそうなら「板が十分に埋まっていれば実板を使う」という条件分岐だけで
ガミの伸びしろ（実質的中 現状〜31% → オラクル 48.8%）の一部が取れる。

## 測り方

`wt_odds_snapshot` の各時点（morning / h10 / h12 / h14 / h18 / evening）で、
**買う目すべてが揃っているレース**に限り、その板で配分したときの成績を出す。
比較対象は同じレースでの 予測オッズ / p3のみ / オラクル（確定オッズ）。

⚠️ **母集団を必ず揃える**（板が揃うレースだけで全案を評価する）。揃えないと
   「板が揃うレース＝人気で堅い」という選択効果を効果と誤認する。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_board_vs_predicted_by_time.py \
        --from 2026-06-13 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.odds_prediction import (  # noqa: E402
    OddsPredictionUnavailable, predicted_trio_board,
)
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.stake_allocation import allocate_budget  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN, rank_7c_buy_plan,
    rank_7c_is_lowpay_pattern, rank_7c_select_axis, rank_7c_select_legs,
)

BUDGET = 10_000
SNAPS = ("morning", "h10", "h12", "h14", "h18", "evening")


def _parse(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2026-06-13")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    a = ap.parse_args()

    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, "
            "       r.race_type, r.cup_grade "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (a.d1, a.d2))
        ent, meta = defaultdict(dict), {}
        for rk, fn, p3, pw, fo, mk, lg, rt, g in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0,
                                    pw=float(pw) / 100.0 if pw is not None else None,
                                    fo=fo, mark=mk, lg=lg)
            meta[rk] = (rt, g)
        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value FROM wt_odds o "
            "JOIN wt_races r USING(race_key) WHERE r.race_date BETWEEN ? AND ? "
            "  AND r.n_entries=7 AND o.bet_type='trio' AND o.odds_value>0", (a.d1, a.d2))
        final = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            final[rk][frozenset(_parse(cb))] = float(od)
        cur = conn.execute(
            "SELECT s.race_key, s.snapshot_type, s.combination, s.odds_value "
            "FROM wt_odds_snapshot s JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries=7 "
            "  AND s.bet_type='trio' AND s.odds_value>0 AND s.odds_value<9000", (a.d1, a.d2))
        snap = defaultdict(lambda: defaultdict(dict))
        for rk, st, cb, od in cur.fetchall():
            snap[rk][st][frozenset(_parse(cb))] = float(od)

    rows = []
    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in final:
            continue
        if sum(1 for v in cars.values() if v["fo"] in (1, 2, 3)) != 3:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        pw = ({f: v["pw"] for f, v in cars.items()}
              if all(v["pw"] is not None for v in cars.values()) else None)
        sel = rank_7c_select_axis(p3)
        if sel is None:
            continue
        a1, a2, _ = sel
        if calibrated_p3_sum_top2(p3, *meta[rk]) < RANK_7C_P3_SUM_MIN:
            continue
        legs_all = rank_7c_select_legs(sorted(set(p3) - {a1, a2}), p3)
        if len(legs_all) < RANK_7C_LEGS_MIN:
            continue
        if rank_7c_is_lowpay_pattern(p3, {f: v["lg"] for f, v in cars.items()}):
            continue
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        plan = rank_7c_buy_plan(p3, pw, a1, legs_all, wt_ana=marks.get(4))
        if plan is None or plan[0] != "trio":
            continue
        legs = plan[1]
        combos = {t: frozenset({a1, a2, t}) for t in legs}
        if not all(c in final[rk] for c in combos.values()):
            continue
        try:
            pb = predicted_trio_board(rk)
        except (OddsPredictionUnavailable, Exception):
            continue
        pred = {t: pb.get(c) for t, c in combos.items()}
        if not all(pred.values()):
            continue
        rows.append(dict(rk=rk, p3=p3, legs=legs, combos=combos, pred=pred,
                         fin={t: final[rk][c] for t, c in combos.items()},
                         snap={st: {t: snap[rk][st].get(c) for t, c in combos.items()}
                               for st in SNAPS if st in snap.get(rk, {})},
                         win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3)),
                         wleg=next((t for t, c in combos.items()
                                    if c == frozenset(f for f, v in cars.items()
                                                      if v["fo"] in (1, 2, 3))), None)))

    print(f"\n7C（三連複・確定オッズと予測が揃う）{len(rows)}R [{a.d1}〜{a.d2}]")

    def run(name, sub, wfn):
        n = hit = net = 0
        bet = pay = 0
        for r in sub:
            w = wfn(r)
            if w is None:
                continue
            st = allocate_budget(w, BUDGET)
            b = sum(st.values())
            n += 1; bet += b
            if r["wleg"] is not None:
                p = int(r["fin"][r["wleg"]] * st[r["wleg"]])
                pay += p; hit += 1
                if p >= b:
                    net += 1
        if not n:
            return
        print(f"    {name:22}{n:>6}{100*hit/n:>10.1f}{100*net/n:>11.1f}{100*pay/bet:>8.1f}")

    HEAD = f"    {'':22}{'R':>6}{'素の的中%':>10}{'実質的中%':>11}{'ROI%':>8}"

    for st in SNAPS:
        sub = [r for r in rows if st in r["snap"] and all(r["snap"][st].values())]
        if len(sub) < 50:
            continue
        print(f"\n  ===== {st} の板が買う目すべてに揃うレース（{len(sub)}R）=====")
        print(HEAD)
        run("p3 のみ", sub, lambda r: {t: r["p3"][t] for t in r["legs"]})
        run("予測オッズ（現行）", sub, lambda r: {t: 1/r["pred"][t] for t in r["legs"]})
        run(f"{st} の実板", sub, lambda r: {t: 1/r["snap"][st][t] for t in r["legs"]})
        run("実板×予測の相乗平均", sub,
            lambda r: {t: (1/r["snap"][st][t])**0.5 * (1/r["pred"][t])**0.5
                       for t in r["legs"]})
        run("確定オッズ（オラクル）", sub, lambda r: {t: 1/r["fin"][t] for t in r["legs"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
