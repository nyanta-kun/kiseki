#!/usr/bin/env python3
"""7C: 想定払戻帯ごとの成績（入稿時に分かる情報だけで帯を作る）・2026-08-19。

## 目的

ユーザー判断（2026-08-19）:
  「日中の硬そうなレースでもガミ回避可能程度の予想で母数があれば売上がつき、
    的中率の積み上げにも効くと考えていたが、的中率も圧倒的でなく売れていない。
    このレンジは入稿対象から除外すべき。**的中精度は同程度を確実に**」

除外の閾値を切るために、**入稿時点で計算できる量**で帯を作って成績を見る。

    想定払戻倍率（下限） = min_i (賭け金_i × 朝の板オッズ_i) / 予算

傾斜配分は払戻をそろえる向きに効くので、この最小値が「当たったとき最悪でも
何倍返るか」。1.0 を下回るとどの目が来てもガミになりうる。

🔴 **朝の板（`wt_odds_snapshot` の morning）は 2026-06-08 以降しか無い。**
   売上データ（`netkeirin_sales_race`）は 2026-08-01 以降で、帯ごとに 18〜24件しか
   無い。**売上だけで閾値を切ってはいけない**（このリポジトリが繰り返している事故）。
   ここでは的中側を最大の窓で測り、売上は方向の確認にだけ使う。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_expected_payout_band.py \
        --from 2026-06-08 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.rebuild_stakes import stakes_for_combos  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN, rank_7c_buy_plan,
    rank_7c_is_lowpay_pattern, rank_7c_select_axis, rank_7c_select_legs,
)

BUDGET = 10_000
BANDS = [(0, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 3.0), (3.0, 1e9)]


def _parse(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2026-06-08")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    a = ap.parse_args()

    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, "
            "       r.race_type, r.cup_grade, r.start_at "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (a.d1, a.d2))
        ent, meta = defaultdict(dict), {}
        for rk, fn, p3, pw, fo, mk, lg, rt, g, sa in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0,
                                    pw=float(pw) / 100.0 if pw is not None else None,
                                    fo=fo, mark=mk, lg=lg)
            meta[rk] = (rt, g, sa)
        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value FROM wt_odds o "
            "JOIN wt_races r USING(race_key) WHERE r.race_date BETWEEN ? AND ? "
            "  AND r.n_entries=7 AND o.bet_type='trio' AND o.odds_value>0", (a.d1, a.d2))
        trio = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            trio[rk][frozenset(_parse(cb))] = float(od)
        cur = conn.execute(
            "SELECT s.race_key, s.combination, s.odds_value FROM wt_odds_snapshot s "
            "JOIN wt_races r USING(race_key) WHERE r.race_date BETWEEN ? AND ? "
            "  AND r.n_entries=7 AND s.bet_type='trio' AND s.snapshot_type='morning' "
            "  AND s.odds_value>0", (a.d1, a.d2))
        board = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            board[rk][frozenset(_parse(cb))] = float(od)
        cur = conn.execute(
            "SELECT race_key, n_sold, sold_paid_points FROM netkeirin_sales_race "
            "WHERE race_key IS NOT NULL")
        sold = {r[0]: (int(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()}

    rows = []
    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in trio or rk not in board:
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
        if calibrated_p3_sum_top2(p3, meta[rk][0], meta[rk][1]) < RANK_7C_P3_SUM_MIN:
            continue
        legs_all = rank_7c_select_legs(sorted(set(p3) - {a1, a2}), p3)
        if len(legs_all) < RANK_7C_LEGS_MIN:
            continue
        if rank_7c_is_lowpay_pattern(p3, {f: v["lg"] for f, v in cars.items()}):
            continue
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        plan = rank_7c_buy_plan(p3, pw, a1, legs_all, wt_ana=marks.get(4))
        if plan is None or plan[0] != "trio":
            continue                       # 三連単切替は別設計なので対象外
        legs = plan[1]
        combos = [frozenset({a1, a2, t}) for t in legs]
        if not all(c in trio[rk] and c in board[rk] for c in combos):
            continue
        st = stakes_for_combos(a1, a2, combos, p3, board=board[rk], budget=BUDGET)
        bet = sum(st.values())
        exp_lo = min(board[rk][c] * st[c] / bet for c in combos)
        win = frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3))
        pay = int(trio[rk][win] * st[win]) if win in st else 0
        rows.append(dict(rk=rk, date=rk[:8], exp_lo=exp_lo, bet=bet, pay=pay,
                         hit=pay > 0, net=pay >= bet and pay > 0,
                         sold=sold.get(rk)))

    days = len({r["date"] for r in rows})
    print(f"\n7C（三連複のみ）{len(rows)}R / {days}日 [{a.d1}〜{a.d2}]")
    print("  想定払戻(下限) = min_i(賭け金_i × 朝の板オッズ_i) / 予算")
    print(f"\n  {'想定払戻(下限)':16}{'R':>6}{'件/日':>7}{'素の的中%':>10}{'表示的中%':>11}"
          f"{'ROI%':>8}{'倍率中央':>9}{'無売上%':>9}{'購入/R':>8}")
    tot_n = len(rows)
    for lo, hi in BANDS:
        b = [r for r in rows if lo <= r["exp_lo"] < hi]
        if not b:
            continue
        rat = [r["pay"] / r["bet"] for r in b if r["hit"]]
        sl = [r for r in b if r["sold"] is not None]
        lbl = f"{lo:.1f}〜{hi:.1f}" if hi < 1e8 else f"{lo:.1f}以上"
        print(f"  {lbl:16}{len(b):>6}{len(b)/days:>7.1f}"
              f"{100*sum(r['hit'] for r in b)/len(b):>10.1f}"
              f"{100*sum(r['net'] for r in b)/len(b):>11.1f}"
              f"{100*sum(r['pay'] for r in b)/sum(r['bet'] for r in b):>8.1f}"
              f"{(statistics.median(rat) if rat else 0):>9.2f}"
              f"{(100*sum(1 for r in sl if r['sold'][0]==0)/len(sl) if sl else 0):>9.1f}"
              f"{(sum(r['sold'][0] for r in sl)/len(sl) if sl else 0):>8.2f}"
              + (f"   (売上n={len(sl)})" if sl else "   (売上データ無)"))

    # 閾値を切ったときに残る側がどうなるか（「的中精度は同程度」の確認）
    print(f"\n===== 想定払戻が閾値未満を除外したときの『残る側』 =====")
    print(f"  {'閾値':10}{'除外R':>7}{'除外%':>7}{'残R':>7}{'残 素の的中%':>13}"
          f"{'残 表示的中%':>13}{'残 ROI%':>9}")
    base_net = 100*sum(r["net"] for r in rows)/tot_n
    base_hit = 100*sum(r["hit"] for r in rows)/tot_n
    base_roi = 100*sum(r["pay"] for r in rows)/sum(r["bet"] for r in rows)
    print(f"  {'（除外なし）':10}{0:>7}{0:>7.1f}{tot_n:>7}{base_hit:>13.1f}"
          f"{base_net:>13.1f}{base_roi:>9.1f}")
    for th in (1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6):
        keep = [r for r in rows if r["exp_lo"] >= th]
        drop = tot_n - len(keep)
        if not keep:
            continue
        print(f"  {'>= ' + str(th):10}{drop:>7}{100*drop/tot_n:>7.1f}{len(keep):>7}"
              f"{100*sum(r['hit'] for r in keep)/len(keep):>13.1f}"
              f"{100*sum(r['net'] for r in keep)/len(keep):>13.1f}"
              f"{100*sum(r['pay'] for r in keep)/sum(r['bet'] for r in keep):>9.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
