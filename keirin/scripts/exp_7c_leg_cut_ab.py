#!/usr/bin/env python3
"""7C: 相手の絞り（`rank_7c_cut_legs_by_gap`）を ON/OFF して同一レースで比べる（2026-08-19）。

## なぜ

全ランクの外れ方を分解したところ、**7C の最大の外れ要因は軸ではなく相手（3列目）**
だった（`exp_miss_anatomy_all_ranks.py`・6,710R）:

    hit 31.6% / **leg_out 30.8%** / axis2_out 21.8% / axis1_out 8.8% / both_out 3.7%

軸2車がそろって3着内に入るのは 65.7% で、そのうち買い目が当たるのは 48.1%。
**軸が当たったレースの半分以上を3列目で落としている。**

実データの点数は 2点 1,916R / 3点 1,560R / 4点 2,428R / 5点 820R で、
**半分以上が2〜3点**（`RANK_7C_LEGS_MIN=4` は選別の条件で、買う点は
`rank_7c_buy_plan` → `rank_7c_cut_legs_by_gap` がさらに削る）。

🔴 **点数別の集計から買い方を決めてはいけない**（欠車・レース性質と交絡する。
   [[keirin_line_structure_2026_08_18]]）。だから**同一レースで絞りを ON/OFF**する。

## 比較

| 名前 | 相手 |
|---|---|
| 現行 | `rank_7c_buy_plan`（= gap 絞り + △削り） |
| 絞りOFF | `rank_7c_select_legs` の結果をそのまま全部 |

予算は同じ 10,000円。配分は本番の再構築と同じ `stakes_for_combos`（board=None）。
採点は確定オッズ。`pred_top3_pct` は月次凍結 vintage。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_leg_cut_ab.py --from 2025-01-01 --to 2026-08-18
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


def load(d1, d2):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, "
            "       r.race_type, r.cup_grade "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (d1, d2))
        ent, meta = defaultdict(dict), {}
        for rk, fn, p3, pw, fo, mk, lg, rt, g in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0,
                                    pw=float(pw) / 100.0 if pw is not None else None,
                                    fo=fo, mark=mk, lg=lg)
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
    return ent, meta, trio, tfc


def load_board(d1, d2):
    """朝の板（`wt_odds_snapshot` の morning）。**2026-06-08 以降のみ存在**。"""
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT s.race_key, s.combination, s.odds_value "
            "FROM wt_odds_snapshot s JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND s.bet_type = 'trio' AND s.snapshot_type = 'morning' "
            "  AND s.odds_value > 0", (d1, d2))
        out = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            out[rk][frozenset(_parse(cb))] = float(od)
    return out


class Acc:
    def __init__(self):
        self.n = self.bet = self.pay = self.hit = self.disp = 0
        self.ratios = []
        self.per_race = {}

    def add(self, rk, bet, pay):
        self.n += 1; self.bet += bet; self.pay += pay
        self.per_race[rk] = (bet, pay)
        if pay > 0:
            self.hit += 1
            self.ratios.append(pay / bet)
            if pay >= bet:
                self.disp += 1

    def row(self):
        if not self.n:
            return "  （0件）"
        med = statistics.median(self.ratios) if self.ratios else 0
        return (f"{self.n:>7}{100*self.hit/self.n:>9.1f}{100*self.disp/self.n:>10.1f}"
                f"{100*(self.hit-self.disp)/self.hit if self.hit else 0:>8.1f}"
                f"{100*self.pay/self.bet:>8.1f}{med:>9.2f}"
                f"{statistics.mean([len(x) for x in self.npts]):>7.2f}"
                if hasattr(self, "npts") else
                f"{self.n:>7}{100*self.hit/self.n:>9.1f}{100*self.disp/self.n:>10.1f}"
                f"{100*(self.hit-self.disp)/self.hit if self.hit else 0:>8.1f}"
                f"{100*self.pay/self.bet:>8.1f}{med:>9.2f}")


HEAD = f"  {'':22}{'R':>7}{'素の的中%':>9}{'実質的中%':>10}{'ガミ%':>8}{'ROI%':>8}{'倍率中央':>9}"


def boot(a, b, keys, n_iter=2000, seed=31):
    rnd = random.Random(seed); d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        ab = sum(a.per_race[k][0] for k in s); ap = sum(a.per_race[k][1] for k in s)
        bb = sum(b.per_race[k][0] for k in s); bp = sum(b.per_race[k][1] for k in s)
        if ab and bb:
            d.append(100*bp/bb - 100*ap/ab)
    d.sort()
    return d[int(.025*len(d))], d[int(.975*len(d))]


def boot_rate(a, b, keys, n_iter=2000, seed=37):
    rnd = random.Random(seed)
    fa = {k: int(a.per_race[k][1] >= a.per_race[k][0] and a.per_race[k][1] > 0) for k in keys}
    fb = {k: int(b.per_race[k][1] >= b.per_race[k][0] and b.per_race[k][1] > 0) for k in keys}
    d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        d.append(100*(sum(fb[k] for k in s) - sum(fa[k] for k in s))/len(s))
    d.sort()
    return d[int(.025*len(d))], d[int(.975*len(d))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    # 🔴 朝の板を配分に入れる（本番の傾斜配分に近づける）。
    #    絞りを導入した当時の検証は**本番と同じ傾斜配分**で行われており、
    #    p3 のみで測った A/B とは前提が違う。`wt_odds_snapshot` の morning は
    #    **2026-06-08 以降しか無い**ので、この経路では窓が短くなる。
    ap.add_argument("--board", action="store_true", help="朝の板を配分に使う")
    a = ap.parse_args()
    ent, meta, trio, tfc = load(a.d1, a.d2)
    board = load_board(a.d1, a.d2) if a.board else {}
    if a.board:
        print(f"\n朝の板を配分に使用（{len(board)}Rぶん取得）")

    rows = []
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
        a1, a2, _ = sel
        if calibrated_p3_sum_top2(p3, *meta.get(rk, (None, None))) < RANK_7C_P3_SUM_MIN:
            continue
        legs_all = rank_7c_select_legs(sorted(set(p3) - {a1, a2}), p3)
        if len(legs_all) < RANK_7C_LEGS_MIN:
            continue
        if rank_7c_is_lowpay_pattern(p3, {f: v["lg"] for f, v in cars.items()}):
            continue
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        plan = rank_7c_buy_plan(p3, pw, a1, legs_all, wt_ana=marks.get(4))
        if plan is None:
            continue
        rows.append(dict(rk=rk, date=rk[:8], p3=p3, a1=a1, a2=a2,
                         kind=plan[0], legs_cur=plan[1], legs_all=legs_all,
                         trio=trio[rk], tfc=tfc.get(rk, {}),
                         win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3)),
                         order=tuple(sorted((f for f, v in cars.items()
                                             if v["fo"] in (1, 2, 3)),
                                            key=lambda f: cars[f]["fo"]))))
    days = len({r["date"] for r in rows})
    print(f"\n7C 再現 {len(rows)}R / {days}日 ({len(rows)/days:.1f}件per日) [{a.d1}〜{a.d2}]")
    npts = defaultdict(int)
    for r in rows:
        npts[len(r["legs_cur"])] += 1
    print("  現行の点数分布: " + " / ".join(f"{k}点 {v}R" for k, v in sorted(npts.items())))

    def score(r, legs):
        if r["kind"] == "trifecta":
            st = unit_stake(len(legs), BUDGET)
            bet = st * len(legs)
            want = {(r["a1"], r["a2"], t) for t in legs}
            if r["order"] in want and r["order"] in r["tfc"]:
                return bet, int(r["tfc"][r["order"]] * st)
            return bet, 0
        combos = [frozenset({r["a1"], r["a2"], t}) for t in legs]
        if not all(c in r["trio"] for c in combos):
            return None
        st = stakes_for_combos(r["a1"], r["a2"], combos, r["p3"],
                               board=(board.get(r["rk"]) or None), budget=BUDGET)
        bet = sum(st.values())
        w = r["win"]
        return (bet, int(r["trio"][w] * st[w])) if w in st else (bet, 0)

    print(f"\n===== 相手の絞り ON/OFF（同一レース・同一予算）=====")
    print(HEAD)
    cur, off = Acc(), Acc()
    keys = []
    for r in rows:
        s1, s2 = score(r, r["legs_cur"]), score(r, r["legs_all"])
        if s1 is None or s2 is None:
            continue
        keys.append(r["rk"])
        cur.add(r["rk"], *s1)
        off.add(r["rk"], *s2)
    print(f"  {'現行（絞りあり）':22}{cur.row()}")
    print(f"  {'絞りOFF（全点買う）':22}{off.row()}")
    lo, hi = boot(cur, off, keys)
    dl, dh = boot_rate(cur, off, keys)
    print(f"  差（OFF − 現行）: ROI {100*off.pay/off.bet - 100*cur.pay/cur.bet:+6.1f}pt "
          f"[{lo:+6.1f},{hi:+6.1f}]{'*' if lo>0 or hi<0 else ''}"
          f"   実質的中 {100*(off.disp-cur.disp)/len(keys):+5.1f}pt "
          f"[{dl:+5.1f},{dh:+5.1f}]{'*' if dl>0 or dh<0 else ''}")

    # 買い目が変わったレースだけ
    ch = [r for r in rows if r["rk"] in cur.per_race
          and set(r["legs_cur"]) != set(r["legs_all"])]
    if ch:
        print(f"\n===== 買い目が変わったレースだけ（{len(ch)}R）=====")
        print(HEAD)
        c2, o2 = Acc(), Acc()
        for r in ch:
            c2.add(r["rk"], *score(r, r["legs_cur"]))
            o2.add(r["rk"], *score(r, r["legs_all"]))
        print(f"  {'現行（絞りあり）':22}{c2.row()}")
        print(f"  {'絞りOFF':22}{o2.row()}")
        k2 = [r["rk"] for r in ch]
        lo, hi = boot(c2, o2, k2); dl, dh = boot_rate(c2, o2, k2)
        print(f"  差（OFF − 現行）: ROI {100*o2.pay/o2.bet - 100*c2.pay/c2.bet:+6.1f}pt "
              f"[{lo:+6.1f},{hi:+6.1f}]{'*' if lo>0 or hi<0 else ''}"
              f"   実質的中 {100*(o2.disp-c2.disp)/len(k2):+5.1f}pt "
              f"[{dl:+5.1f},{dh:+5.1f}]{'*' if dl>0 or dh<0 else ''}")

    print("\n===== 年別 =====")
    for y in ("2025", "2026"):
        sub = [r for r in rows if r["date"].startswith(y) and r["rk"] in cur.per_race]
        if not sub:
            continue
        c3, o3 = Acc(), Acc()
        for r in sub:
            c3.add(r["rk"], *score(r, r["legs_cur"]))
            o3.add(r["rk"], *score(r, r["legs_all"]))
        print(f"\n  [{y}] {len(sub)}R"); print(HEAD)
        print(f"  {'現行':22}{c3.row()}")
        print(f"  {'絞りOFF':22}{o3.row()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
