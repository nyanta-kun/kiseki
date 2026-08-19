#!/usr/bin/env python3
"""看板穴埋め（7車）: 軸2車が「別ラインの先頭同士」のときの扱い（2026-08-19）。

## 出発点

2026-08-19 立川3R（チャレンジ選抜・穴埋め）の構造:

    ライン  [1-4-6] 先頭1(逃)  /  [3-7] 先頭3(逃)  /  [2]単騎  /  [5]単騎
    指数    1:68.0  3:58.5  4:57.1  7:39.0  6:33.4  2:32.7  5:22.8
    印      ◎=3  ◯=1
    軸      1 と 3  ←（指数上位2車＝別ラインの先頭同士＝ともに逃）
    決着    4-2-7  ← 先頭2車が**共倒れ**し、番手と単騎で決まった

「先頭同士がやり合って潰れる」構造は発走前に読める、というのが仮説。
1レースでは何も決まらないので全期間で検定する。

## 定義

    duel  = 現行の軸2車が **ともにライン先頭（line_size>=2）で、別ライン**
    拮抗  = 指数上位3車の p3 が近い（top1 - top3 が小さい）

## 買い方

すべて三連複・軸2頭ながし5点・予算10,000円・`stakes_for_combos`（board=None）。
採点は確定オッズ。`pred_top3_pct` は月次凍結 vintage（リーク無し）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_marquee_fill_7car_duel.py \
        --from 2025-01-01 --to 2026-08-18
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


def _parse_combo(s: str) -> list[int]:
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def load(date_from: str, date_to: str):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT race_key, race_date, race_no, race_type, cup_id, cup_grade "
            "FROM wt_races WHERE race_date BETWEEN ? AND ?", (date_from, date_to))
        by_cup: dict[tuple, list[dict]] = defaultdict(list)
        for rk, d, no, rtype, cup, grade in cur.fetchall():
            by_cup[(d, cup)].append(dict(race_key=rk, race_no=int(no),
                                         race_type=rtype, cup_grade=grade))
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, e.line_size, "
            "       e.is_line_leader, e.style "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (date_from, date_to))
        ent: dict[str, dict] = defaultdict(dict)
        for rk, fn, p3, pw, fo, mark, lg, ls, leader, style in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0,
                                    pw=float(pw) / 100.0 if pw is not None else None,
                                    fo=fo, mark=mark, lg=lg, ls=ls,
                                    leader=leader, style=style)
        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value "
            "FROM wt_odds o JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND o.bet_type = 'trio'", (date_from, date_to))
        trio: dict[str, dict] = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            trio[rk][frozenset(_parse_combo(cb))] = float(od)
    return by_cup, ent, trio


class Acc:
    def __init__(self) -> None:
        self.n = self.bet = self.pay = self.hit = self.two = self.hit10 = 0
        self.ratios: list[float] = []
        self.per_race: dict[str, tuple[int, int]] = {}

    def add(self, rk, bet, pay, odds, two) -> None:
        self.n += 1
        self.bet += bet
        self.pay += pay
        self.two += int(two)
        self.per_race[rk] = (bet, pay)
        if pay > 0:
            self.hit += 1
            self.ratios.append(pay / bet)
            if odds is not None and odds >= 10.0:
                self.hit10 += 1

    def row(self) -> str:
        if not self.n:
            return "  （0件）"
        med = statistics.median(self.ratios) if self.ratios else 0.0
        disp = sum(1 for r in self.ratios if r > 1)
        return (f"{self.n:>7}{100 * self.two / self.n:>8.1f}{100 * self.hit / self.n:>8.1f}"
                f"{100 * disp / self.n:>8.1f}{100 * self.pay / self.bet:>8.1f}"
                f"{med:>9.2f}{100 * self.hit10 / self.n:>9.1f}")


HEAD = (f"  {'':28}{'R':>7}{'二軸%':>8}{'的中%':>8}{'表示%':>8}{'ROI%':>8}"
        f"{'配当中央':>9}{'10倍+%':>9}")


def score(a1, a2, p3, win, od):
    legs = [f for f in p3 if f not in (a1, a2)]
    combos = [frozenset({a1, a2, t}) for t in legs]
    st = stakes_for_combos(a1, a2, combos, p3, board=None, budget=BUDGET)
    bet = sum(st.values())
    if win in st and win in od:
        return bet, int(od[win] * st[win]), od[win]
    return bet, 0, None


def boot(base: Acc, new: Acc, keys, n_iter=2000, seed=11):
    rnd = random.Random(seed)
    d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        bb = sum(base.per_race[k][0] for k in s); bp = sum(base.per_race[k][1] for k in s)
        nb = sum(new.per_race[k][0] for k in s); np_ = sum(new.per_race[k][1] for k in s)
        if bb and nb:
            d.append(100 * np_ / nb - 100 * bp / bb)
    d.sort()
    return d[int(0.025 * len(d))], d[int(0.975 * len(d))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-08-18")
    args = ap.parse_args()

    by_cup, ent, trio = load(args.date_from, args.date_to)
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
        p3 = {f: v["p3"] for f, v in cars.items()}
        order = sorted(p3, key=lambda f: -p3[f])
        a1, a2 = order[0], order[1]

        def is_leader(n):
            v = cars[n]
            return bool(v["leader"] and (v["ls"] or 1) > 1)

        def deputy(head):
            g = cars[head]["lg"]
            for n in order:
                if n != head and cars[n]["lg"] == g:
                    return n
            return None

        rows.append(dict(
            rk=rk, date=rk[:8], p3=p3, order=order, a1=a1, a2=a2, cars=cars,
            od=trio[rk], win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3)),
            duel=(is_leader(a1) and is_leader(a2)
                  and cars[a1]["lg"] != cars[a2]["lg"]),
            nige_duel=(is_leader(a1) and is_leader(a2)
                       and cars[a1]["lg"] != cars[a2]["lg"]
                       and cars[a1]["style"] == "逃" and cars[a2]["style"] == "逃"),
            gap13=p3[order[0]] - p3[order[2]],
            gap23=p3[order[1]] - p3[order[2]],
            dep1=deputy(a1), dep2=deputy(a2),
            marks={v["mark"]: f for f, v in cars.items() if v["mark"]},
            is_leader=is_leader,
        ))

    days = len({r["date"] for r in rows})
    print(f"\n穴埋め帯（7車）: {len(rows)}R / {days}日 [{args.date_from}〜{args.date_to}]")

    # ---- 構造の頻度と成績 --------------------------------------------
    print("\n===== 現行軸（指数上位2車）の構造別 =====")
    print(HEAD)
    groups = {
        "全体": lambda r: True,
        "duel（別ライン先頭同士）": lambda r: r["duel"],
        "  うち 逃×逃": lambda r: r["nige_duel"],
        "  うち 拮抗(gap13<0.10)": lambda r: r["duel"] and r["gap13"] < 0.10,
        "duel でない": lambda r: not r["duel"],
    }
    for name, fn in groups.items():
        acc = Acc()
        for r in rows:
            if not fn(r):
                continue
            b, p, o = score(r["a1"], r["a2"], r["p3"], r["win"], r["od"])
            acc.add(r["rk"], b, p, o, {r["a1"], r["a2"]} <= r["win"])
        print(f"  {name:28}{acc.row()}")

    # ---- duel 内で「先頭が実際に飛ぶ」頻度 ----------------------------
    duel_rows = [r for r in rows if r["duel"]]
    nod_rows = [r for r in rows if not r["duel"]]
    def both_out(rs):
        return 100 * sum(1 for r in rs if not ({r["a1"], r["a2"]} & r["win"])) / len(rs)
    print(f"\n  軸2車がそろって3着外れる率: duel {both_out(duel_rows):.1f}%"
          f"  /  duelでない {both_out(nod_rows):.1f}%")

    # ---- duel 専用の軸ルール ------------------------------------------
    def rule_current(r):
        return r["a1"], r["a2"]

    def rule_deputies(r):
        """D2: 先頭2車を降ろし、それぞれの番手を軸にする。"""
        if r["dep1"] and r["dep2"] and r["dep1"] != r["dep2"]:
            return r["dep1"], r["dep2"]
        return r["a1"], r["a2"]

    def rule_head_deputy(r):
        """D3: 軸1はそのまま・軸2をその番手へ（9車で採用しているライン組み替え）。"""
        return (r["a1"], r["dep1"]) if r["dep1"] else (r["a1"], r["a2"])

    def rule_nonleader_top2(r):
        """D4: 先頭を全部外し、非先頭の指数上位2車を軸にする。"""
        rest = [f for f in r["order"] if not r["is_leader"](f)]
        return (rest[0], rest[1]) if len(rest) >= 2 else (r["a1"], r["a2"])

    def rule_drop_axis2(r):
        """D5: 軸1は残し、軸2をライン先頭以外の指数最上位へ。"""
        rest = [f for f in r["order"] if f != r["a1"] and not r["is_leader"](f)]
        return (r["a1"], rest[0]) if rest else (r["a1"], r["a2"])

    DUEL_RULES = {
        "D1 現行（先頭2車）": rule_current,
        "D2 番手2車へ": rule_deputies,
        "D3 軸1+その番手": rule_head_deputy,
        "D4 非先頭の上位2車": rule_nonleader_top2,
        "D5 軸1+非先頭の最上位": rule_drop_axis2,
    }

    for label, subset in (("duel 全体", duel_rows),
                          ("duel × 逃×逃", [r for r in rows if r["nige_duel"]]),
                          ("duel × 拮抗(gap13<0.10)",
                           [r for r in duel_rows if r["gap13"] < 0.10]),
                          ("duel × ◎◯が軸2車",
                           [r for r in duel_rows
                            if r["marks"].get(1) and r["marks"].get(2)
                            and {r["a1"], r["a2"]} == {r["marks"][1], r["marks"][2]}])):
        if not subset:
            continue
        print(f"\n===== {label}（{len(subset)}R・{len(subset)/days:.2f}件/日）=====")
        print(HEAD)
        accs = {}
        for name, fn in DUEL_RULES.items():
            acc = Acc()
            for r in subset:
                a1, a2 = fn(r)
                b, p, o = score(a1, a2, r["p3"], r["win"], r["od"])
                acc.add(r["rk"], b, p, o, {a1, a2} <= r["win"])
            accs[name] = acc
            print(f"  {name:28}{acc.row()}")
        base = accs["D1 現行（先頭2車）"]
        keys = [r["rk"] for r in subset]
        for name, acc in accs.items():
            if name.startswith("D1"):
                continue
            lo, hi = boot(base, acc, keys)
            d = 100 * acc.pay / acc.bet - 100 * base.pay / base.bet
            print(f"  {name:28} ROI {d:+6.1f}pt [{lo:+6.1f},{hi:+6.1f}]"
                  f"{'  有意' if lo > 0 or hi < 0 else ''}")

    # ---- 年別（duel 全体） --------------------------------------------
    print("\n===== 年別の再現性（duel 全体）=====")
    for year in ("2025", "2026"):
        sub = [r for r in duel_rows if r["date"].startswith(year)]
        if not sub:
            continue
        print(f"\n  [{year}] {len(sub)}R")
        print(HEAD)
        for name, fn in DUEL_RULES.items():
            acc = Acc()
            for r in sub:
                a1, a2 = fn(r)
                b, p, o = score(a1, a2, r["p3"], r["win"], r["od"])
                acc.add(r["rk"], b, p, o, {a1, a2} <= r["win"])
            print(f"  {name:28}{acc.row()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
