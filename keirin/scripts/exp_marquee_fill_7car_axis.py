#!/usr/bin/env python3
"""看板穴埋め（7車）の軸選定を見直す（2026-08-19）。

## なぜ

穴埋め（`submit_marquee_wt.py`）は**ランクのゲートを一切通っていない**。
7車の軸は `ai_rank`（= pred_top3 降順）の上位2車をそのまま使うだけで、
7S 本体が持つ

  - `wt_overlap_n <= 1`（軸2車が WT ◎◯の**両方**と一致するものは除外）
  - `axis_sum <= RANK_7S_AXIS_SUM_MAX`
  - `entropy <= RANK_7S_ENTROPY_MAX`

のどれも見ていない。ラベルだけ 7S を借りている。
9車は 2026-08-16（PR#193）にライン組み替えを入れたが、**7車は未測定のまま**。

## 何を測るか

穴埋め帯（`marquee_race_nos`）の7車レースで、軸の決め方を入れ替えて
同一母集団・同一予算で買い比べる。

⚠️ `wt_entries.pred_top3_pct` は `backfill_index_pct_wt.py` が月次凍結 vintage
   モデルで書いた値（リーク無し）。採点は確定オッズ。配分は本番の再構築と同じ
   `rebuild_stakes.stakes_for_combos`（board=None）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_marquee_fill_7car_axis.py \
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
            "SELECT race_key, race_date, race_no, race_type, cup_id, cup_grade, n_entries "
            "FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        )
        races = {}
        by_cup: dict[tuple, list[dict]] = defaultdict(list)
        for rk, d, no, rtype, cup, grade, ne in cur.fetchall():
            r = dict(race_key=rk, race_date=d, race_no=int(no), race_type=rtype,
                     cup_id=cup, cup_grade=grade, n_entries=ne)
            races[rk] = r
            by_cup[(d, cup)].append(r)

        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, e.line_size, "
            "       e.is_line_leader "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL",
            (date_from, date_to),
        )
        ent: dict[str, dict] = defaultdict(dict)
        for rk, fn, p3, pw, fo, mark, lg, ls, leader in cur.fetchall():
            ent[rk][int(fn)] = dict(
                p3=float(p3) / 100.0,
                pw=float(pw) / 100.0 if pw is not None else None,
                fo=fo, mark=mark, lg=lg, ls=ls, leader=leader)

        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value "
            "FROM wt_odds o JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND o.bet_type = 'trio'",
            (date_from, date_to),
        )
        trio: dict[str, dict] = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            trio[rk][frozenset(_parse_combo(cb))] = float(od)

        cur = conn.execute(
            "SELECT DISTINCT split_part(race_key, '#', 1) FROM picks_history "
            "WHERE race_date BETWEEN ? AND ?", (date_from, date_to))
        ranked = {r[0] for r in cur.fetchall()}
    return races, by_cup, ent, trio, ranked


# --------------------------------------------------------------------------
# 軸の決め方
# --------------------------------------------------------------------------
def axes_current(p3: dict[int, float], marks: dict[int, int], **_) -> tuple[int, int]:
    """現行: 指数（p3）上位2車。"""
    o = sorted(p3, key=lambda f: -p3[f])
    return o[0], o[1]


def _swap_axis2(p3, a1, a2, banned) -> tuple[int, int]:
    """軸2を banned 以外の p3 最上位へ差し替える（居なければそのまま）。"""
    rest = [f for f in sorted(p3, key=lambda f: -p3[f]) if f != a1 and f not in banned]
    return (a1, rest[0]) if rest else (a1, a2)


def axes_no_double_mark(p3, marks, **_) -> tuple[int, int]:
    """R1: 軸2車が ◎◯ の両方と一致するときだけ、軸2を◎◯以外へ差し替える。"""
    a1, a2 = axes_current(p3, marks)
    h, t = marks.get(1), marks.get(2)
    if h is None or t is None:
        return a1, a2
    if {a1, a2} == {h, t}:
        return _swap_axis2(p3, a1, a2, {h, t})
    return a1, a2


def axes_keep_honmei_drop_taikou(p3, marks, **_) -> tuple[int, int]:
    """R2: ◎は軸に残す・◯とは重ねない（[[keirin_axis_mark_overlap_highpay]] の型）。

    ◯が軸に入っていたら、◎◯以外の p3 最上位へ差し替える。◎は触らない。
    """
    a1, a2 = axes_current(p3, marks)
    h, t = marks.get(1), marks.get(2)
    if h is None or t is None:
        return a1, a2
    if t == a1 and h == a2:          # ◯が軸1・◎が軸2 → ◎を軸1へ寄せて◯を外す
        return _swap_axis2(p3, h, a1, {h, t})
    if t == a2:
        return _swap_axis2(p3, a1, a2, {t})
    if t == a1:
        return _swap_axis2(p3, a2, a1, {t})
    return a1, a2


def axes_no_mark_axis2(p3, marks, **_) -> tuple[int, int]:
    """R3: 軸2は必ず◎◯以外から取る（軸1は指数1位のまま）。"""
    a1, a2 = axes_current(p3, marks)
    h, t = marks.get(1), marks.get(2)
    if h is None or t is None:
        return a1, a2
    return _swap_axis2(p3, a1, a2, {h, t})


def axes_line(p3, marks, lines=None, **_) -> tuple[int, int]:
    """R4: 9車で採用しているライン組み替えを7車へ持ち込む（未検証の移植）。"""
    a1, a2 = axes_current(p3, marks)
    ln = lines or {}
    order = sorted(p3, key=lambda f: -p3[f])
    if not all(f in ln for f in order):
        return a1, a2

    def is_leader(n):
        e = ln.get(n)
        return bool(e and e["leader"] and (e["ls"] or 1) > 1)

    def same_line(head):
        g = ln[head]["lg"]
        for n in order:
            if n != head and ln.get(n) and ln[n]["lg"] == g:
                return n
        return None

    if is_leader(a1):
        p = same_line(a1)
        if p is not None:
            return a1, p
    elif is_leader(a2):
        p = same_line(a2)
        if p is not None:
            return a2, p
    return a1, a2


RULES = {
    "R0 現行(指数上位2車)": axes_current,
    "R1 ◎◯二重のみ軸2差替": axes_no_double_mark,
    "R2 ◎は残す/◯と重ねない": axes_keep_honmei_drop_taikou,
    "R3 軸2は常に◎◯以外": axes_no_mark_axis2,
    "R4 ライン組み替え(9車移植)": axes_line,
}


class Acc:
    def __init__(self) -> None:
        self.n = self.bet = self.pay = self.hit = self.two = self.hit10 = 0
        self.ratios: list[float] = []
        self.per_race: dict[str, tuple[int, int]] = {}

    def add(self, rk, bet, pay, odds, two_axis) -> None:
        self.n += 1
        self.bet += bet
        self.pay += pay
        self.two += int(two_axis)
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


HEAD = (f"  {'':30}{'R':>7}{'二軸%':>8}{'的中%':>8}{'表示%':>8}{'ROI%':>8}"
        f"{'配当中央':>9}{'10倍+%':>9}")


def score(a1, a2, p3, win, od) -> tuple[int, int, float | None]:
    legs = [f for f in p3 if f not in (a1, a2)]
    combos = [frozenset({a1, a2, t}) for t in legs]
    stakes = stakes_for_combos(a1, a2, combos, p3, board=None, budget=BUDGET)
    bet = sum(stakes.values())
    if win in stakes and win in od:
        return bet, int(od[win] * stakes[win]), od[win]
    return bet, 0, None


def paired_bootstrap(base: Acc, new: Acc, keys: list[str], n_iter=2000, seed=7):
    """レース単位 paired bootstrap で ROI 差の 95%CI を返す。"""
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n_iter):
        smp = [keys[rnd.randrange(len(keys))] for _ in keys]
        b_bet = sum(base.per_race[k][0] for k in smp)
        b_pay = sum(base.per_race[k][1] for k in smp)
        n_bet = sum(new.per_race[k][0] for k in smp)
        n_pay = sum(new.per_race[k][1] for k in smp)
        if b_bet and n_bet:
            diffs.append(100 * n_pay / n_bet - 100 * b_pay / b_bet)
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-08-18")
    ap.add_argument("--only-unranked", action="store_true",
                    help="どのランクも取っていないレースだけ（穴埋めが実際に出る側の近似）")
    args = ap.parse_args()

    races, by_cup, ent, trio, ranked = load(args.date_from, args.date_to)

    # 穴埋め帯を再現する
    band: set[str] = set()
    for key, rs in by_cup.items():
        want = marquee_race_nos(rs)
        for r in rs:
            if r["race_no"] in want:
                band.add(r["race_key"])

    rows = []
    for rk in sorted(band):
        cars = ent.get(rk)
        if not cars or len(cars) != 7 or rk not in trio:
            continue
        if sum(1 for v in cars.values() if v["fo"] in (1, 2, 3)) != 3:
            continue
        if args.only_unranked and rk in ranked:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        pw = {f: v["pw"] for f, v in cars.items()}
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        lines = {f: v for f, v in cars.items()}
        rows.append(dict(rk=rk, p3=p3, pw=pw, marks=marks, lines=lines,
                         win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3)),
                         od=trio[rk], date=rk[:8]))

    print(f"\n穴埋め帯（7車）: {len(rows)}R / {len({r['date'] for r in rows})}日 "
          f"[{args.date_from}〜{args.date_to}]"
          f"{'  ※ランク未採用のみ' if args.only_unranked else ''}")

    # ---- 診断: 現行軸の ◎◯重なり別 -------------------------------------
    print("\n===== 診断: 現行軸（指数上位2車）と WT◎◯ の重なり別 =====")
    print(HEAD)
    by_ov: dict[object, Acc] = defaultdict(Acc)
    for r in rows:
        a1, a2 = axes_current(r["p3"], r["marks"])
        h, t = r["marks"].get(1), r["marks"].get(2)
        ov = None if (h is None or t is None) else len({a1, a2} & {h, t})
        b, p, o = score(a1, a2, r["p3"], r["win"], r["od"])
        by_ov[ov].add(r["rk"], b, p, o, {a1, a2} <= r["win"])
    for k in [0, 1, 2, None]:
        if by_ov[k].n:
            print(f"  {'重なり=' + str(k):30}{by_ov[k].row()}")

    # ---- 軸ルールの比較 ------------------------------------------------
    print("\n===== 軸ルールの比較（同一母集団・同一予算）=====")
    print(HEAD)
    accs: dict[str, Acc] = {}
    changed: dict[str, set] = {}
    for name, fn in RULES.items():
        acc = Acc()
        ch = set()
        for r in rows:
            a1, a2 = fn(r["p3"], r["marks"], lines=r["lines"])
            if {a1, a2} != set(axes_current(r["p3"], r["marks"])):
                ch.add(r["rk"])
            b, p, o = score(a1, a2, r["p3"], r["win"], r["od"])
            acc.add(r["rk"], b, p, o, {a1, a2} <= r["win"])
        accs[name] = acc
        changed[name] = ch
        print(f"  {name:30}{acc.row()}  変更{len(ch)}R")

    base = accs["R0 現行(指数上位2車)"]
    keys = [r["rk"] for r in rows]
    print("\n  ROI差の95%CI（レース単位 paired bootstrap・vs R0）")
    for name, acc in accs.items():
        if name.startswith("R0"):
            continue
        lo, hi = paired_bootstrap(base, acc, keys)
        d = 100 * acc.pay / acc.bet - 100 * base.pay / base.bet
        print(f"  {name:30} ROI {d:+6.1f}pt  [{lo:+6.1f}, {hi:+6.1f}]"
              f"{'  有意' if lo > 0 or hi < 0 else ''}")

    # 変更が起きたレースだけで比べる（薄まりを除く）
    print("\n===== 変更が起きたレースだけ =====")
    print(HEAD)
    for name, acc in accs.items():
        if name.startswith("R0"):
            continue
        ch = changed[name]
        if not ch:
            continue
        sub_b, sub_n = Acc(), Acc()
        for r in rows:
            if r["rk"] not in ch:
                continue
            a1, a2 = axes_current(r["p3"], r["marks"])
            b, p, o = score(a1, a2, r["p3"], r["win"], r["od"])
            sub_b.add(r["rk"], b, p, o, {a1, a2} <= r["win"])
            n1, n2 = RULES[name](r["p3"], r["marks"], lines=r["lines"])
            b, p, o = score(n1, n2, r["p3"], r["win"], r["od"])
            sub_n.add(r["rk"], b, p, o, {n1, n2} <= r["win"])
        print(f"  {'  現行 ← ' + name:30}{sub_b.row()}")
        print(f"  {'  新   ← ' + name:30}{sub_n.row()}")
        lo, hi = paired_bootstrap(sub_b, sub_n, sorted(ch))
        d = 100 * sub_n.pay / sub_n.bet - 100 * sub_b.pay / sub_b.bet
        print(f"  {'':30} ROI {d:+6.1f}pt  [{lo:+6.1f}, {hi:+6.1f}]"
              f"{'  有意' if lo > 0 or hi < 0 else ''}\n")

    # ---- 年別の再現性 --------------------------------------------------
    print("===== 年別の再現性 =====")
    for year in ("2025", "2026"):
        sub = [r for r in rows if r["date"].startswith(year)]
        if not sub:
            continue
        print(f"\n  [{year}] {len(sub)}R")
        print(HEAD)
        for name, fn in RULES.items():
            acc = Acc()
            for r in sub:
                a1, a2 = fn(r["p3"], r["marks"], lines=r["lines"])
                b, p, o = score(a1, a2, r["p3"], r["win"], r["od"])
                acc.add(r["rk"], b, p, o, {a1, a2} <= r["win"])
            print(f"  {name:30}{acc.row()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
