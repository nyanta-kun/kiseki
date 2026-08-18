#!/usr/bin/env python3
"""「堅い帯 × ◎あり・○なし」を 7M1 型の相手（下位3車）で買う案の検証（2026-08-19）。

## 何を確かめるか

2026-08-19 の調査で、三連複の払戻10倍以上を狙うなら
**「◎は軸に残し、○とは重ねない」**のが最適と分かった（軸を差し替えると
10倍以上の的中頻度が半減する）。その母集団のうち

- 混戦帯（上位2車の p3 合計 < RANK_7C_P3_SUM_MIN）… **既に 7M1 が取っている**
- 堅い帯（>= RANK_7C_P3_SUM_MIN）           … **7C が相手総流しで拾っている**

本スクリプトは後者について、**同じレース**を
「現行 7C の買い方」と「7M1 型の相手（下位3車）」で買い比べる。

🔴 **件数を揃えた比較である**（同一レース集合・投資額も同じ予算枠）。
   過去に「少なく賭けただけ」を改善と誤認した例があるため
   （[[keirin_race_selection_meta]]）、母集団は動かさない。

⚠️ 採点は**確定オッズ**。入稿は8時なので実運用の板とは違う。
   配分は `rebuild_stakes.stakes_for_combos`（board=None）＝本番の再構築と同じ
   p3 傾斜。予測オッズは渡さない（model-vintage look-ahead になるため）。

⚠️ `wt_entries.pred_top3_pct` は `backfill_index_pct_wt.py` が月次凍結 vintage
   モデルで書いた値なのでリークは無い（各月をtest窓として学習したモデル）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7m1_firm_band.py
        --from 2025-01-01 --to 2026-08-18
        --wiring   # 本番の `rank_7m1_daily_select` を ON/OFF して増分を出す
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
import src.strategy_wt as sw  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_AXIS1_P3_MAX,
    RANK_7C_LEGS_MIN,
    RANK_7C_P3_SUM_MIN,
    rank_7c_buy_plan,
    rank_7c_is_lowpay_pattern,
    rank_7c_select_axis,
    rank_7c_select_legs,
    rank_7m1_daily_select,
    rank_7m1_select_legs,
    rank_7s_wt_overlap_n,
    unit_stake,
)

BUDGET = 10_000


def _parse_combo(s: str) -> list[int]:
    """`1-2-3` / `1=2=3` の両表記を吸収する（2026-06 に表記が変わっている）。"""
    return [int(x) for x in re.split(r"[-=>]+", s) if x.strip().isdigit()]


def load(date_from: str, date_to: str):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, "
            "       r.race_type, r.cup_grade "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL",
            (date_from, date_to),
        )
        ent: dict[str, dict] = defaultdict(dict)
        meta: dict[str, tuple] = {}
        for rk, fn, p3, pw, fo, mark, lg, rtype, grade in cur.fetchall():
            ent[rk][fn] = dict(p3=float(p3) / 100.0,
                               pw=float(pw) / 100.0 if pw is not None else None,
                               fo=fo, mark=mark, lg=lg)
            meta[rk] = (rtype, grade)
        cur = conn.execute(
            "SELECT o.race_key, o.bet_type, o.combination, o.odds_value "
            "FROM wt_odds o JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND o.bet_type IN ('trio','trifecta')",
            (date_from, date_to),
        )
        trio: dict[str, dict] = defaultdict(dict)
        tfc: dict[str, dict] = defaultdict(dict)
        for rk, bt, cb, od in cur.fetchall():
            p = _parse_combo(cb)
            if bt == "trio":
                trio[rk][frozenset(p)] = float(od)
            else:
                tfc[rk][tuple(p)] = float(od)
    return ent, meta, trio, tfc


class Acc:
    """1つの買い方の成績を貯める。"""

    def __init__(self) -> None:
        self.n = self.bet = self.pay = self.hit = self.hit10 = 0
        self.ratios: list[float] = []

    def add(self, bet: int, pay: int, odds: float | None) -> None:
        self.n += 1
        self.bet += bet
        self.pay += pay
        if pay > 0:
            self.hit += 1
            self.ratios.append(pay / bet)
            if odds is not None and odds >= 10.0:
                self.hit10 += 1

    def row(self, days: int) -> str:
        if not self.n:
            return "  （0件）"
        med = statistics.median(self.ratios) if self.ratios else 0.0
        disp = sum(1 for r in self.ratios if r > 1)
        return (f"{self.n:>7}{self.n / days:>7.1f}{100 * self.hit / self.n:>7.1f}"
                f"{100 * disp / self.n:>8.1f}{100 * self.pay / self.bet:>7.1f}"
                f"{med:>9.2f}{100 * self.hit10 / self.n:>9.2f}"
                f"{100 * self.hit10 / self.hit if self.hit else 0:>10.1f}")


HEAD = (f"  {'':26}{'R':>7}{'件/日':>7}{'的中%':>7}{'表示%':>8}{'ROI%':>7}"
        f"{'配当中央':>9}{'10倍+%':>9}{'的中中10+':>10}")


def score_trio(a1: int, a2: int, legs: list[int], p3: dict[int, float],
               win: frozenset[int], od: dict) -> tuple[int, int, float | None]:
    combos = [frozenset({a1, a2, t}) for t in legs]
    stakes = stakes_for_combos(a1, a2, combos, p3, board=None, budget=BUDGET)
    bet = sum(stakes.values())
    if win in stakes and win in od:
        return bet, int(od[win] * stakes[win] / 100.0 * 100), od[win]
    return bet, 0, None


def score_trifecta(a1: int, a2: int, legs: list[int], order: tuple,
                   od: dict) -> tuple[int, int, float | None]:
    st = unit_stake(len(legs), BUDGET)
    bet = st * len(legs)
    want = {(a1, a2, t) for t in legs}
    if order in want and order in od:
        return bet, int(od[order] * st), od[order]
    return bet, 0, None


def report_wiring(ent: dict, meta: dict, trio: dict) -> None:
    """本番の `rank_7m1_daily_select` を ON/OFF して増分を測る。

    🔴 実験の数字と**本番の配線**が一致していることの確認。過去に
       「候補dictへキーを載せ忘れて再構築だけ別物になる」事故が繰り返し
       起きているので、判定関数を直接叩いて増分を出しておく。
    """
    cands = []
    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in trio:
            continue
        if sum(1 for v in cars.values() if v["fo"] in (1, 2, 3)) != 3:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        pw = ({f: v["pw"] for f, v in cars.items()}
              if all(v["pw"] is not None for v in cars.values()) else None)
        sel = rank_7c_select_axis(p3)
        if sel is None:
            continue
        a1, a2, raw = sel
        others = sorted(set(p3) - {a1, a2})
        legs_7c = rank_7c_select_legs(others, p3)
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        plan = rank_7c_buy_plan(p3, pw, a1, legs_7c, wt_ana=marks.get(4))
        cands.append({
            "race_key": rk, "n_entries": 7, "axis1_7c": a1, "axis2_7c": a2,
            "p3_sum_top2": raw,
            "p3_sum_top2_cal": calibrated_p3_sum_top2(p3, *meta.get(rk, (None, None))),
            "legs_7c": legs_7c,
            "legs_7c_buy": (plan[1] if plan else None),
            "bet_kind_7c": (plan[0] if plan else None),
            "lowpay_pattern": rank_7c_is_lowpay_pattern(
                p3, {f: v["lg"] for f, v in cars.items()}),
            "axis1_p3": p3[a1],
            "wt_overlap_7c_n": rank_7s_wt_overlap_n(a1, a2, marks.get(1), marks.get(2)),
            "wt_honmei_in_axis_7c": ((marks[1] in (a1, a2)) if marks.get(1) else None),
            "legs_7m1": rank_7m1_select_legs(others, p3),
            "_p3": p3,
            "_win": frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3)),
            "_od": trio[rk],
        })
    days = len({c["race_key"][:8] for c in cands})

    def measure(sub: list[dict]) -> Acc:
        acc = Acc()
        for c in sub:
            acc.add(*score_trio(c["axis1_7c"], c["axis2_7c"], c["legs_7m1"],
                                c["_p3"], c["_win"], c["_od"]))
        return acc

    picked: dict[bool, set] = {}
    print("\n===== 本番配線（rank_7m1_daily_select）=====")
    print(HEAD)
    for flag in (False, True):
        sw.RANK_7M1_FIRM_BAND = flag
        got = rank_7m1_daily_select(cands)
        picked[flag] = {c["race_key"] for c in got}
        print(f"  {'FIRM_BAND=' + str(flag):26}{measure(got).row(days)}")
    sw.RANK_7M1_FIRM_BAND = True
    added = picked[True] - picked[False]
    assert picked[False] <= picked[True], "既存の母集団が減っている（回帰）"
    print(f"  {'増分だけ':26}"
          f"{measure([c for c in cands if c['race_key'] in added]).row(days)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-08-18")
    ap.add_argument("--wiring", action="store_true",
                    help="本番の rank_7m1_daily_select を ON/OFF して増分を出す")
    args = ap.parse_args()

    ent, meta, trio, tfc = load(args.date_from, args.date_to)
    acc: dict[tuple[str, str, str], Acc] = defaultdict(Acc)
    skips: dict[tuple[str, str, str], int] = defaultdict(int)
    days: dict[str, set] = defaultdict(set)

    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in trio:
            continue
        finished = [f for f, v in cars.items() if v["fo"] in (1, 2, 3)]
        if len(finished) != 3:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        pw = ({f: v["pw"] for f, v in cars.items()}
              if all(v["pw"] is not None for v in cars.values()) else None)
        sel = rank_7c_select_axis(p3)
        if sel is None:
            continue
        a1, a2, _raw_sum = sel
        rtype, grade = meta.get(rk, (None, None))
        p3_sum = calibrated_p3_sum_top2(p3, rtype, grade)
        others = [f for f in cars if f not in (a1, a2)]
        legs_7c = rank_7c_select_legs(others, p3)
        legs_m1 = rank_7m1_select_legs(others, p3)
        lowpay = rank_7c_is_lowpay_pattern(p3, {f: v["lg"] for f, v in cars.items()})
        # --- 7C が実際に買うレースか（本番と同じ条件）。買わない理由も残す ---
        if p3_sum < RANK_7C_P3_SUM_MIN:
            continue                      # 混戦帯＝既に 7M1 の担当
        skip = None
        if len(legs_7c) < RANK_7C_LEGS_MIN:
            skip = "相手不足"
        elif lowpay:
            skip = "低配当パターン"
        elif p3[a1] >= RANK_7C_AXIS1_P3_MAX:
            skip = "軸1が抜けすぎ(>=0.93)"
        honmei = next((f for f, v in cars.items() if v["mark"] == 1), None)
        taikou = next((f for f, v in cars.items() if v["mark"] == 2), None)
        ana = next((f for f, v in cars.items() if v["mark"] == 4), None)
        overlap = rank_7s_wt_overlap_n(a1, a2, honmei, taikou)
        if overlap is None:
            continue
        if honmei in (a1, a2) and taikou not in (a1, a2):
            seg = "◎あり・○なし"
        elif overlap == 2:
            seg = "◎○ 完全一致"
        else:
            seg = "その他"
        plan = rank_7c_buy_plan(p3, pw, a1, legs_7c, wt_ana=ana)
        if skip is None and plan is None:
            skip = "三連複ゲート"
        kind, buy = plan if plan is not None else ("trio", [])
        win = frozenset(finished)
        order = tuple(sorted(finished, key=lambda f: cars[f]["fo"]))
        yr = rk[:4]
        days[yr].add(rk[:8])

        if skip is None:
            if kind == "trifecta":
                cur = score_trifecta(a1, a2, buy, order, tfc.get(rk, {}))
            else:
                cur = score_trio(a1, a2, buy, p3, win, trio[rk])
            acc[(seg, yr, "① 7Cが買う: 現行の買い方")].add(*cur)
            if len(legs_m1) >= 2:
                acc[(seg, yr, "② 7Cが買う: 下位3車に替える")].add(
                    *score_trio(a1, a2, legs_m1, p3, win, trio[rk]))
        else:
            skips[(seg, yr, skip)] += 1
            if len(legs_m1) >= 2:
                acc[(seg, yr, f"③ 7Cが見送り[{skip}]: 下位3車")].add(
                    *score_trio(a1, a2, legs_m1, p3, win, trio[rk]))
                acc[(seg, yr, "③ 7Cが見送り(合計): 下位3車")].add(
                    *score_trio(a1, a2, legs_m1, p3, win, trio[rk]))

    print(f"対象期間 {args.date_from} 〜 {args.date_to} / 7車・7Cが買うレースのみ\n")
    labels = sorted({k[2] for k in acc})
    for seg in ("◎あり・○なし", "◎○ 完全一致"):
        print(f"===== {seg} =====")
        print(HEAD)
        for label in labels:
            for yr in sorted(days):
                a = acc.get((seg, yr, label))
                if a and a.n >= 30:
                    print(f"  {label + ' ' + yr:26}{a.row(len(days[yr]))}")
        print()
    if args.wiring:
        report_wiring(ent, meta, trio)
    print("\n7C が見送った理由の内訳（堅い帯のみ）")
    for (seg, yr, why), n in sorted(skips.items()):
        print(f"  {seg:16}{yr}  {why:20}{n:>6}件  {n / len(days[yr]):.2f}件/日")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
