#!/usr/bin/env python3
"""看板穴埋め（7車）: ガミの正体は軸ではなく相手5点の総流し（2026-08-19）。

診断（`exp_marquee_fill_7car_axis.py`）で、穴埋め帯の 76〜90% は
**軸2車が WT ◎◯ の両方と一致**しており、そこは

    二軸的中 52% / 生の的中 52% / **表示的中 28%** / 配当中央 **1.08倍**

＝当たっても賭け金を割る（ガミ）帯だった。軸を差し替えると配当中央は
1.08→1.44 に上がるが、**的中が 16pt・表示的中が 4.6pt 落ちる**ので
看板（売上加重の的中率が目的関数）には合わない。

そこで軸は動かさず、**相手の点数**を振って表示的中とROIを見る。
予算は 10,000円 固定なので、点数を削るほど1点あたりが厚くなりガミが減る。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_marquee_fill_7car_legs.py \
        --from 2025-01-01 --to 2026-08-18 [--only-unranked]
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
from src.marquee import marquee_race_nos  # noqa: E402
from src.rebuild_stakes import stakes_for_combos  # noqa: E402

BUDGET = 10_000


def _parse_combo(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def load(d1, d2):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT race_key, race_date, race_no, race_type, cup_id, cup_grade "
            "FROM wt_races WHERE race_date BETWEEN ? AND ?", (d1, d2))
        by_cup = defaultdict(list)
        for rk, d, no, rt, cup, g in cur.fetchall():
            by_cup[(d, cup)].append(dict(race_key=rk, race_no=int(no),
                                         race_type=rt, cup_grade=g))
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order, "
            "       e.prediction_mark, e.line_group, e.line_size, e.is_line_leader "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (d1, d2))
        ent = defaultdict(dict)
        for rk, fn, p3, fo, mk, lg, ls, ld in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=fo, mark=mk,
                                    lg=lg, ls=ls, leader=ld)
        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value "
            "FROM wt_odds o JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND o.bet_type = 'trio'", (d1, d2))
        trio = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            trio[rk][frozenset(_parse_combo(cb))] = float(od)
        cur = conn.execute(
            "SELECT DISTINCT split_part(race_key, '#', 1) FROM picks_history "
            "WHERE race_date BETWEEN ? AND ?", (d1, d2))
        ranked = {r[0] for r in cur.fetchall()}
    return by_cup, ent, trio, ranked


class Acc:
    def __init__(self):
        self.n = self.bet = self.pay = self.hit = self.hit10 = 0
        self.ratios = []
        self.per_race = {}

    def add(self, rk, bet, pay, odds):
        self.n += 1; self.bet += bet; self.pay += pay
        self.per_race[rk] = (bet, pay)
        if pay > 0:
            self.hit += 1
            self.ratios.append(pay / bet)
            if odds is not None and odds >= 10.0:
                self.hit10 += 1

    def row(self):
        if not self.n:
            return "  （0件）"
        med = statistics.median(self.ratios) if self.ratios else 0.0
        disp = sum(1 for r in self.ratios if r > 1)
        gami = 100 * (self.hit - disp) / self.n
        return (f"{self.n:>7}{100 * self.hit / self.n:>8.1f}{100 * disp / self.n:>8.1f}"
                f"{gami:>8.1f}{100 * self.pay / self.bet:>8.1f}{med:>9.2f}"
                f"{100 * self.hit10 / self.n:>9.1f}")


HEAD = (f"  {'':26}{'R':>7}{'的中%':>8}{'表示%':>8}{'ガミ%':>8}{'ROI%':>8}"
        f"{'配当中央':>9}{'10倍+%':>9}")


def boot(base, new, keys, n_iter=2000, seed=17):
    rnd = random.Random(seed); d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        bb = sum(base.per_race[k][0] for k in s); bp = sum(base.per_race[k][1] for k in s)
        nb = sum(new.per_race[k][0] for k in s); np_ = sum(new.per_race[k][1] for k in s)
        if bb and nb:
            d.append(100 * np_ / nb - 100 * bp / bb)
    d.sort()
    return d[int(.025 * len(d))], d[int(.975 * len(d))]


def boot_disp(base, new, keys, n_iter=2000, seed=19):
    """表示的中率（払戻>賭け金）の差の CI。"""
    rnd = random.Random(seed)
    bf = {k: int(base.per_race[k][1] > base.per_race[k][0]) for k in keys}
    nf = {k: int(new.per_race[k][1] > new.per_race[k][0]) for k in keys}
    d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        d.append(100 * (sum(nf[k] for k in s) - sum(bf[k] for k in s)) / len(s))
    d.sort()
    return d[int(.025 * len(d))], d[int(.975 * len(d))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    ap.add_argument("--only-unranked", action="store_true")
    a = ap.parse_args()

    by_cup, ent, trio, ranked = load(a.d1, a.d2)
    band = set()
    for _k, rs in by_cup.items():
        want = marquee_race_nos(rs)
        band |= {r["race_key"] for r in rs if r["race_no"] in want}

    rows = []
    for rk in sorted(band):
        cars = ent.get(rk)
        if not cars or len(cars) != 7 or rk not in trio:
            continue
        if sum(1 for v in cars.values() if v["fo"] in (1, 2, 3)) != 3:
            continue
        if a.only_unranked and rk in ranked:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        order = sorted(p3, key=lambda f: -p3[f])

        def is_leader(n):
            v = cars[n]
            return bool(v["leader"] and (v["ls"] or 1) > 1)

        def same_line_top(head):
            g = cars[head]["lg"]
            return next((n for n in order if n != head and cars[n]["lg"] == g), None)

        a1, a2 = order[0], order[1]
        # R4（9車で採用済みのライン組み替え）
        la1, la2 = a1, a2
        if is_leader(a1):
            p = same_line_top(a1)
            if p:
                la1, la2 = a1, p
        elif is_leader(a2):
            p = same_line_top(a2)
            if p:
                la1, la2 = a2, p
        rows.append(dict(rk=rk, date=rk[:8], p3=p3, order=order, a1=a1, a2=a2,
                         la1=la1, la2=la2, od=trio[rk],
                         win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3))))

    days = len({r["date"] for r in rows})
    print(f"\n穴埋め帯（7車）: {len(rows)}R / {days}日 [{a.d1}〜{a.d2}]"
          f"{'  ※ランク未採用のみ' if a.only_unranked else ''}")

    def score(a1, a2, legs, p3, win, od):
        combos = [frozenset({a1, a2, t}) for t in legs]
        st = stakes_for_combos(a1, a2, combos, p3, board=None, budget=BUDGET)
        bet = sum(st.values())
        if win in st and win in od:
            return bet, int(od[win] * st[win]), od[win]
        return bet, 0, None

    def legs_top(r, k, axes):
        rest = [f for f in r["order"] if f not in axes]
        return rest[:k]

    def legs_bottom(r, k, axes):
        rest = [f for f in r["order"] if f not in axes]
        return rest[-k:]

    PLANS = {
        "L0 現行 5点総流し": ("cur", lambda r, ax: legs_top(r, 5, ax)),
        "L1 相手 上位4点": ("cur", lambda r, ax: legs_top(r, 4, ax)),
        "L2 相手 上位3点": ("cur", lambda r, ax: legs_top(r, 3, ax)),
        "L3 相手 上位2点": ("cur", lambda r, ax: legs_top(r, 2, ax)),
        "L4 相手 下位3点(7M1型)": ("cur", lambda r, ax: legs_bottom(r, 3, ax)),
        "L5 ライン軸 + 5点": ("line", lambda r, ax: legs_top(r, 5, ax)),
        "L6 ライン軸 + 上位3点": ("line", lambda r, ax: legs_top(r, 3, ax)),
    }

    for label, subset in (("全体", rows),
                          ("◎◯が軸2車（重なり2）", None)):
        if subset is None:
            continue
        print(f"\n===== {label} =====")
        print(HEAD)
        accs = {}
        for name, (axkind, legfn) in PLANS.items():
            acc = Acc()
            for r in subset:
                ax = (r["a1"], r["a2"]) if axkind == "cur" else (r["la1"], r["la2"])
                legs = legfn(r, set(ax))
                b, p, o = score(ax[0], ax[1], legs, r["p3"], r["win"], r["od"])
                acc.add(r["rk"], b, p, o)
            accs[name] = acc
            print(f"  {name:26}{acc.row()}")
        base = accs["L0 現行 5点総流し"]
        keys = [r["rk"] for r in subset]
        print("\n  vs L0（レース単位 paired bootstrap）")
        for name, acc in accs.items():
            if name.startswith("L0"):
                continue
            lo, hi = boot(base, acc, keys)
            dlo, dhi = boot_disp(base, acc, keys)
            droi = 100 * acc.pay / acc.bet - 100 * base.pay / base.bet
            ddisp = (100 * sum(1 for k in keys if acc.per_race[k][1] > acc.per_race[k][0]) / len(keys)
                     - 100 * sum(1 for k in keys if base.per_race[k][1] > base.per_race[k][0]) / len(keys))
            print(f"  {name:26} ROI {droi:+6.1f}pt [{lo:+6.1f},{hi:+6.1f}]"
                  f"{'*' if lo > 0 or hi < 0 else ' '}"
                  f"   表示 {ddisp:+5.1f}pt [{dlo:+5.1f},{dhi:+5.1f}]"
                  f"{'*' if dlo > 0 or dhi < 0 else ' '}")

    print("\n===== 年別の再現性 =====")
    for year in ("2025", "2026"):
        sub = [r for r in rows if r["date"].startswith(year)]
        if not sub:
            continue
        print(f"\n  [{year}] {len(sub)}R")
        print(HEAD)
        for name, (axkind, legfn) in PLANS.items():
            acc = Acc()
            for r in sub:
                ax = (r["a1"], r["a2"]) if axkind == "cur" else (r["la1"], r["la2"])
                legs = legfn(r, set(ax))
                b, p, o = score(ax[0], ax[1], legs, r["p3"], r["win"], r["od"])
                acc.add(r["rk"], b, p, o)
            print(f"  {name:26}{acc.row()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
