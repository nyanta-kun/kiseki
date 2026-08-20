#!/usr/bin/env python3
"""7S と 7M1 の入稿優先順位を測り直す（2026-08-19）。

## なぜ測り直すか

`RANK_ORDER` は **7H2 > 7S > 7C > 7T1 > 7B > 7H1 > 7M1** で 7M1 が最下位。
根拠は 7M1 新設時（2026-08-17）の直接対決

    競合2,068R  両方的中240 / 7M1のみ29 / 7S のみ575
    的中 13.0%(7M1) vs 39.4%(7S) ・ ROI 80.8 vs 78.4
    → 「7M1 の的中の89%は7Sも当てている＝譲って正しい」

だが **2026-08-19 に `RANK_7M1_FIRM_BAND` で堅い帯を取り込み母集団が変わった**
（+23%）。またユーザーの狙いが「三連複10倍以上を中心に」へ移っている。
判断軸が変わったので、同じ土俵で測り直す。

## 母集団

`picks_history` の **本番記録**（rebuild が本番と同じ判定関数・同じ配分で書いた行）。
7S / 7M1 とも 2025-01〜 で月次件数に不連続が無く、7M1 は単一 rule_version。

🔴 **ROI 単独で採否を決めない。** 7M1 は的中が低く払戻の分散が大きいので
   ROI を ±2.5pt に収めるのに約15.6年かかる（`RANK_7M1_P3_SUM_MAX` 定義部）。
   見るのは 的中率 / 表示的中率 / 払戻倍率の分布。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7s_vs_7m1_priority.py \
        --from 2025-01-01 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402


def load(d1: str, d2: str):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT split_part(race_key,'#',1) AS rk, race_date, rank, "
            "       bet_amount, hit, payout, n_combos, pred_combo "
            "FROM picks_history "
            "WHERE race_date BETWEEN ? AND ? AND rank IN ('RANK_7S','RANK_7M1') "
            "  AND bet_amount > 0", (d1, d2))
        rows: dict[str, dict] = defaultdict(dict)
        for rk, d, rank, bet, hit, pay, n, combo in cur.fetchall():
            rows[rk][rank] = dict(date=d, bet=int(bet or 0), hit=int(hit or 0),
                                  pay=int(pay or 0), n=n, combo=combo)
        # 開催グレード（堅い帯かどうかの層別に使う race_type も一緒に）
        cur = conn.execute(
            "SELECT race_key, race_type, cup_grade FROM wt_races "
            "WHERE race_date BETWEEN ? AND ?", (d1, d2))
        meta = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    return rows, meta


class Acc:
    def __init__(self) -> None:
        self.n = self.bet = self.pay = self.hit = self.disp = 0
        self.ratios: list[float] = []
        self.per_race: dict[str, tuple[int, int]] = {}

    def add(self, rk: str, bet: int, pay: int) -> None:
        self.n += 1
        self.bet += bet
        self.pay += pay
        self.per_race[rk] = (bet, pay)
        if pay > 0:
            self.hit += 1
            self.ratios.append(pay / bet)
            if pay > bet:
                self.disp += 1

    def row(self) -> str:
        if not self.n:
            return "  （0件）"
        med = statistics.median(self.ratios) if self.ratios else 0.0
        x10 = sum(1 for r in self.ratios if r >= 10)
        x5 = sum(1 for r in self.ratios if r >= 5)
        x2p = sum(1 for r in self.ratios if r >= 2)
        x2 = sum(1 for r in self.ratios if r < 2)
        return (f"{self.n:>7}{100 * self.hit / self.n:>8.1f}{100 * self.disp / self.n:>8.1f}"
                f"{100 * x2p / self.n:>8.1f}{100 * x5 / self.n:>8.1f}"
                f"{100 * self.pay / self.bet:>8.1f}{med:>9.2f}"
                f"{100 * x10 / self.n:>9.2f}{100 * x2 / self.hit if self.hit else 0:>10.1f}")


HEAD = (f"  {'':22}{'R':>7}{'的中%':>8}{'表示%':>8}{'2倍+%':>8}{'5倍+%':>8}"
        f"{'ROI%':>8}{'倍率中央':>9}{'10倍+%':>9}{'的中中2倍未満':>10}")


def boot(a: Acc, b: Acc, keys: list[str], n_iter=3000, seed=23):
    """ROI 差（b − a）の 95%CI。"""
    rnd = random.Random(seed)
    d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        ab = sum(a.per_race[k][0] for k in s); ap = sum(a.per_race[k][1] for k in s)
        bb = sum(b.per_race[k][0] for k in s); bp = sum(b.per_race[k][1] for k in s)
        if ab and bb:
            d.append(100 * bp / bb - 100 * ap / ab)
    d.sort()
    return d[int(.025 * len(d))], d[int(.975 * len(d))]


def boot_rate(a: Acc, b: Acc, keys: list[str], kind: str, n_iter=3000, seed=29):
    """的中率 / 表示的中率 / 2倍以上 / 5倍以上 の差（b − a）の 95%CI。

    ⚠️ `kind="x2"` が **2026-08-21 に足した本命の指標**。当初この検証は
    表示的中（払戻>賭け金）で採否を決めていたが、ユーザー方針が
    「的中率そのものには意味がない・件数は減ってよい」へ変わったため、
    **2倍以上で的中した率**を主指標にする必要がある。
    """
    rnd = random.Random(seed)

    def flag(acc, k):
        bet, pay = acc.per_race[k]
        if kind == "disp":
            return int(pay > bet)
        if kind == "x2":
            return int(pay >= 2 * bet and pay > 0)
        if kind == "x5":
            return int(pay >= 5 * bet and pay > 0)
        return int(pay > 0)

    fa = {k: flag(a, k) for k in keys}
    fb = {k: flag(b, k) for k in keys}
    d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        d.append(100 * (sum(fb[k] for k in s) - sum(fa[k] for k in s)) / len(s))
    d.sort()
    return d[int(.025 * len(d))], d[int(.975 * len(d))]


def report(label: str, keys: list[str], rows: dict) -> tuple[Acc, Acc] | None:
    if not keys:
        print(f"\n===== {label}（0件）=====")
        return None
    s, m = Acc(), Acc()
    for k in keys:
        s.add(k, rows[k]["RANK_7S"]["bet"], rows[k]["RANK_7S"]["pay"])
        m.add(k, rows[k]["RANK_7M1"]["bet"], rows[k]["RANK_7M1"]["pay"])
    days = len({rows[k]["RANK_7S"]["date"] for k in keys})
    print(f"\n===== {label}（{len(keys)}R / {len(keys)/days:.2f}件per日）=====")
    print(HEAD)
    print(f"  {'7S を出す（現行）':22}{s.row()}")
    print(f"  {'7M1 を出す':22}{m.row()}")

    both = sum(1 for k in keys if s.per_race[k][1] > 0 and m.per_race[k][1] > 0)
    only_s = sum(1 for k in keys if s.per_race[k][1] > 0 and m.per_race[k][1] == 0)
    only_m = sum(1 for k in keys if s.per_race[k][1] == 0 and m.per_race[k][1] > 0)
    print(f"  内訳: 両方的中 {both} / 7Sのみ {only_s} / 7M1のみ {only_m} / "
          f"どちらも外れ {len(keys) - both - only_s - only_m}")
    if both:
        win_m = sum(1 for k in keys
                    if s.per_race[k][1] > 0 and m.per_race[k][1] > s.per_race[k][1])
        print(f"        両方的中のうち 7M1 の払戻が上 {win_m}/{both} "
              f"({100*win_m/both:.0f}%)・総払戻 7S {sum(s.per_race[k][1] for k in keys):,}円 "
              f"vs 7M1 {sum(m.per_race[k][1] for k in keys):,}円")

    lo, hi = boot(s, m, keys)
    dl, dh = boot_rate(s, m, keys, "disp")
    hl, hh = boot_rate(s, m, keys, "hit")
    print(f"  差（7M1 − 7S）: ROI {100*m.pay/m.bet - 100*s.pay/s.bet:+6.1f}pt "
          f"[{lo:+6.1f},{hi:+6.1f}]{'*' if lo>0 or hi<0 else ' '}"
          f"  表示的中 {100*(m.disp-s.disp)/len(keys):+5.1f}pt [{dl:+5.1f},{dh:+5.1f}]"
          f"{'*' if dl>0 or dh<0 else ' '}"
          f"  的中 {100*(m.hit-s.hit)/len(keys):+5.1f}pt [{hl:+5.1f},{hh:+5.1f}]"
          f"{'*' if hl>0 or hh<0 else ' '}")

    # 🔴 2026-08-21 追加: ユーザー方針変更（的中率そのものには意味がない・
    #    件数は減ってよい）に伴い、**2倍以上/5倍以上で的中した率**を主指標に据える。
    #    当初の不採用根拠だった「表示的中 −20pt」は、その差のほとんどが
    #    **2倍未満の的中**で出来ていたため、KPI を変えると判断が変わりうる。
    def _rate(acc, mult):
        return 100 * sum(1 for k in keys
                         if acc.per_race[k][1] >= mult * acc.per_race[k][0]
                         and acc.per_race[k][1] > 0) / len(keys)
    for mult, kind in ((2, "x2"), (5, "x5")):
        a, b = _rate(s, mult), _rate(m, mult)
        cl, ch = boot_rate(s, m, keys, kind)
        print(f"     └ {mult}倍以上で的中: 7S {a:5.2f}%  7M1 {b:5.2f}%  "
              f"差 {b - a:+5.2f}pt [{cl:+5.2f},{ch:+5.2f}]"
              f"{'*' if cl > 0 or ch < 0 else ' '}")
    return s, m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    args = ap.parse_args()

    rows, meta = load(args.d1, args.d2)
    overlap = sorted(k for k, v in rows.items() if len(v) == 2)
    only_7s = [k for k, v in rows.items() if set(v) == {"RANK_7S"}]
    only_7m1 = [k for k, v in rows.items() if set(v) == {"RANK_7M1"}]

    print(f"\n[{args.d1}〜{args.d2}] 7S {len(only_7s)+len(overlap)}R / "
          f"7M1 {len(only_7m1)+len(overlap)}R / **競合 {len(overlap)}R**"
          f"（7S の {100*len(overlap)/(len(only_7s)+len(overlap)):.0f}% / "
          f"7M1 の {100*len(overlap)/(len(only_7m1)+len(overlap)):.0f}%）")

    report("競合レース全体", overlap, rows)

    for year in ("2025", "2026"):
        report(f"競合 [{year}]",
               [k for k in overlap if rows[k]["RANK_7S"]["date"].startswith(year)], rows)

    # 7M1 の点数（堅い帯の取り込みで2点が増えた）で層別
    for n in (2, 3):
        report(f"競合 × 7M1 が{n}点",
               [k for k in overlap if rows[k]["RANK_7M1"]["n"] == n], rows)

    # 看板クラスかどうか（売上が集まる帯で判断が変わらないか）
    from src.marquee import is_fill_target
    marq = [k for k in overlap if is_fill_target(meta.get(k, (None, None))[0],
                                                 meta.get(k, (None, None))[1])]
    report("競合 × 看板/大会クラス", marq, rows)
    report("競合 × それ以外", [k for k in overlap if k not in set(marq)], rows)

    # 競合しない側（優先順位を変えても動かない部分）
    print("\n===== 競合していないレース（優先順位では動かない）=====")
    print(HEAD)
    for name, keys, rank in (("7S 単独", only_7s, "RANK_7S"),
                             ("7M1 単独", only_7m1, "RANK_7M1")):
        acc = Acc()
        for k in keys:
            acc.add(k, rows[k][rank]["bet"], rows[k][rank]["pay"])
        print(f"  {name:22}{acc.row()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
