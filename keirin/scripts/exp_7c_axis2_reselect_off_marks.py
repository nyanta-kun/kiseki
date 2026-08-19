#!/usr/bin/env python3
"""7C: 軸2車が ◎◯ と完全一致したとき、軸2を ◎◯ 以外から選び直す（2026-08-19）。

## 背景

軸2車が WT ◎◯ と完全一致するのは **7C の 92.8%**（既定状態）。そこで外れると
**88.7% が「片方だけ来た」**で、飛ぶのはほぼ ◯ 側（◎が3着内 87.2% / ◯ 75.6%）。
ただし ◎ はほぼモデル軸1（89.9%）なので、これは「軸2が飛ぶ」の言い換えでもある。

ユーザー判断（2026-08-19）:
  「片方は当たっているので改善の余地がある。◎◯の予想を売って外すのは
    印象が良くない。軸2を WT◯ 以外から再選出するのが良さそう」

## 比較（軸1は据え置き・母集団は不変）

    V0 現行      軸2 = ◯（= モデル軸2）
    V1 1着率     軸2 = ◎◯ を除いた中で `pred_win_pct` 最上位
    V2 3着内率   軸2 = ◎◯ を除いた中で `pred_top3_pct` 最上位

軸2を替えると相手（3列目）も変わるので、**買い目は `rank_7c_buy_plan` で
組み直す**（点数・絞り・△削り・三連単切替まで本番と同じ関数を通す）。
選別ゲート `p3_sum_top2` は上位2車から作るので軸を替えても動かない
＝**件数は完全に同一**（[[keirin_race_selection_meta_2026_08_18]] の教訓）。

## 🔴 配分を本番に揃えること

2026-08-19 に一度踏んだ罠: p3 のみの配分で測ると符号が反転する。
本番は 朝の板 × p3 × 予測オッズ。朝の板（`wt_odds_snapshot` の morning）は
**2026-06-08 以降しか無い**ので、**両方の窓を必ず並べて出す**。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_axis2_reselect_off_marks.py \
        --from 2025-01-01 --to 2026-08-18
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_axis2_reselect_off_marks.py \
        --from 2026-06-08 --to 2026-08-18 --board
"""
from __future__ import annotations

import argparse
import random
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
    unit_stake,
)

BUDGET = 10_000


def _parse(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def load(d1, d2, board=False):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.pred_top2_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, "
            "       r.race_type, r.cup_grade "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL AND e.pred_win_pct IS NOT NULL "
            "  AND e.pred_top2_pct IS NOT NULL",
            (d1, d2))
        ent, meta = defaultdict(dict), {}
        for rk, fn, p3, pw, p2, fo, mk, lg, rt, g in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0, pw=float(pw) / 100.0,
                                    p2=float(p2) / 100.0, fo=fo, mark=mk, lg=lg)
            meta[rk] = (rt, g)
        cur = conn.execute(
            "SELECT o.race_key, o.bet_type, o.combination, o.odds_value "
            "FROM wt_odds o JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND o.bet_type IN ('trio','trifecta') AND o.odds_value > 0", (d1, d2))
        trio, tfc = defaultdict(dict), defaultdict(dict)
        for rk, bt, cb, od in cur.fetchall():
            p = _parse(cb)
            (trio[rk] if bt == "trio" else tfc[rk])[
                frozenset(p) if bt == "trio" else tuple(p)] = float(od)
        bd = defaultdict(dict)
        if board:
            cur = conn.execute(
                "SELECT s.race_key, s.combination, s.odds_value "
                "FROM wt_odds_snapshot s JOIN wt_races r USING(race_key) "
                "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
                "  AND s.bet_type='trio' AND s.snapshot_type='morning' "
                "  AND s.odds_value > 0", (d1, d2))
            for rk, cb, od in cur.fetchall():
                bd[rk][frozenset(_parse(cb))] = float(od)
    return ent, meta, trio, tfc, bd


class Acc:
    def __init__(self):
        self.n = self.bet = self.pay = self.hit = self.disp = self.two = self.x10 = 0
        self.ratios = []
        self.per_race = {}

    def add(self, rk, bet, pay, two):
        self.n += 1; self.bet += bet; self.pay += pay; self.two += int(two)
        self.per_race[rk] = (bet, pay, int(two))
        if pay > 0:
            self.hit += 1
            self.ratios.append(pay / bet)
            if pay >= bet:
                self.disp += 1
            if pay / bet >= 10:
                self.x10 += 1

    def row(self):
        if not self.n:
            return "  （0件）"
        med = statistics.median(self.ratios) if self.ratios else 0
        return (f"{self.n:>7}{100*self.two/self.n:>9.1f}{100*self.hit/self.n:>9.1f}"
                f"{100*self.disp/self.n:>10.1f}{100*self.pay/self.bet:>8.1f}"
                f"{med:>9.2f}{100*self.x10/self.n:>9.2f}")


HEAD = (f"  {'':24}{'R':>7}{'二軸的中%':>9}{'素の的中%':>9}{'表示的中%':>10}"
        f"{'ROI%':>8}{'倍率中央':>9}{'10倍+%':>9}")


def boot(a, b, keys, idx, n_iter=3000, seed=53):
    """idx=None なら ROI 差、idx が整数なら per_race[idx] の率の差（b−a）。"""
    rnd = random.Random(seed); d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        if idx is None:
            ab = sum(a.per_race[k][0] for k in s); ap = sum(a.per_race[k][1] for k in s)
            bb = sum(b.per_race[k][0] for k in s); bp = sum(b.per_race[k][1] for k in s)
            if ab and bb:
                d.append(100*bp/bb - 100*ap/ab)
        else:
            fa = sum(a.per_race[k][idx] for k in s)
            fb = sum(b.per_race[k][idx] for k in s)
            d.append(100*(fb-fa)/len(s))
    d.sort()
    return d[int(.025*len(d))], d[int(.975*len(d))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    ap.add_argument("--board", action="store_true")
    a = ap.parse_args()
    ent, meta, trio, tfc, bd = load(a.d1, a.d2, a.board)

    rows = []
    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in trio:
            continue
        if sum(1 for v in cars.values() if v["fo"] in (1, 2, 3)) != 3:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        pw = {f: v["pw"] for f, v in cars.items()}
        p2 = {f: v["p2"] for f, v in cars.items()}
        sel = rank_7c_select_axis(p3)
        if sel is None:
            continue
        a1, a2, _ = sel
        if calibrated_p3_sum_top2(p3, *meta.get(rk, (None, None))) < RANK_7C_P3_SUM_MIN:
            continue
        if len(rank_7c_select_legs(sorted(set(p3) - {a1, a2}), p3)) < RANK_7C_LEGS_MIN:
            continue
        if rank_7c_is_lowpay_pattern(p3, {f: v["lg"] for f, v in cars.items()}):
            continue
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        h, t = marks.get(1), marks.get(2)
        if h is None or t is None or {a1, a2} != {h, t}:
            continue                     # ◎◯完全一致のレースだけが対象
        rows.append(dict(rk=rk, date=rk[:8], p3=p3, pw=pw, p2=p2, a1=a1, a2=a2,
                         marks=marks, h=h, t=t, trio=trio[rk], tfc=tfc.get(rk, {}),
                         board=bd.get(rk) or None,
                         win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3)),
                         order=tuple(sorted((f for f, v in cars.items()
                                             if v["fo"] in (1, 2, 3)),
                                            key=lambda f: cars[f]["fo"]))))
    days = len({r["date"] for r in rows})
    print(f"\n7C・◎◯完全一致 {len(rows)}R / {days}日 [{a.d1}〜{a.d2}]"
          f"{'  ※朝の板を配分に使用' if a.board else '  ※配分は p3 のみ'}")

    def variants(r):
        pool = [f for f in r["p3"] if f not in (r["a1"], r["h"], r["t"])]
        v = {"V0 現行（軸2=◯）": r["a2"]}
        if pool:
            v["V1 軸2=◎◯以外の1着率1位"] = max(pool, key=lambda f: r["pw"][f])
            v["V2 軸2=◎◯以外の3着内率1位"] = max(pool, key=lambda f: r["p3"][f])
            v["V3 軸2=◎◯以外の2着内率1位"] = max(pool, key=lambda f: r["p2"][f])
        return v

    def score(r, a1, a2):
        legs_all = rank_7c_select_legs(sorted(set(r["p3"]) - {a1, a2}), r["p3"])
        plan = rank_7c_buy_plan(r["p3"], r["pw"], a1, legs_all,
                                wt_ana=r["marks"].get(4))
        if plan is None:
            return None
        kind, legs = plan
        if kind == "trifecta":
            st = unit_stake(len(legs), BUDGET)
            bet = st * len(legs)
            want = {(a1, a2, x) for x in legs}
            pay = int(r["tfc"][r["order"]] * st) if (
                r["order"] in want and r["order"] in r["tfc"]) else 0
            return bet, pay
        combos = [frozenset({a1, a2, x}) for x in legs]
        if not all(c in r["trio"] for c in combos):
            return None
        st = stakes_for_combos(a1, a2, combos, r["p3"],
                               board=r["board"], budget=BUDGET)
        bet = sum(st.values())
        w = r["win"]
        return bet, (int(r["trio"][w] * st[w]) if w in st else 0)

    names = ["V0 現行（軸2=◯）", "V1 軸2=◎◯以外の1着率1位",
             "V3 軸2=◎◯以外の2着内率1位", "V2 軸2=◎◯以外の3着内率1位"]
    accs = {n: Acc() for n in names}
    keys = []
    for r in rows:
        v = variants(r)
        if len(v) < 4:
            continue
        got = {n: score(r, r["a1"], v[n]) for n in names}
        if any(g is None for g in got.values()):
            continue
        keys.append(r["rk"])
        for n in names:
            accs[n].add(r["rk"], *got[n], set([r["a1"], v[n]]) <= r["win"])

    print(f"\n===== 軸2の選び直し（軸1据え置き・同一レース {len(keys)}R・同一予算）=====")
    print(HEAD)
    for n in names:
        print(f"  {n:24}{accs[n].row()}")
    base = accs[names[0]]
    print("\n  vs V0（レース単位 paired bootstrap）")
    for n in names[1:]:
        acc = accs[n]
        r_lo, r_hi = boot(base, acc, keys, None)
        d_lo, d_hi = boot(base, acc, keys, 2)
        droi = 100*acc.pay/acc.bet - 100*base.pay/base.bet
        dtwo = 100*(acc.two - base.two)/len(keys)
        ddisp = 100*(acc.disp - base.disp)/len(keys)
        print(f"  {n:24} ROI {droi:+6.1f}pt [{r_lo:+6.1f},{r_hi:+6.1f}]"
              f"{'*' if r_lo>0 or r_hi<0 else ' '}"
              f"  二軸 {dtwo:+5.1f}pt [{d_lo:+5.1f},{d_hi:+5.1f}]"
              f"{'*' if d_lo>0 or d_hi<0 else ' '}"
              f"  表示的中 {ddisp:+5.1f}pt")

    print("\n===== 年別 =====")
    for y in ("2025", "2026"):
        sub = [r for r in rows if r["date"].startswith(y) and r["rk"] in base.per_race]
        if len(sub) < 100:
            continue
        print(f"\n  [{y}] {len(sub)}R"); print(HEAD)
        for n in names:
            acc = Acc()
            for r in sub:
                v = variants(r)
                g = score(r, r["a1"], v[n])
                if g:
                    acc.add(r["rk"], *g, set([r["a1"], v[n]]) <= r["win"])
            print(f"  {n:24}{acc.row()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
