#!/usr/bin/env python3
"""軸2車が WT ◎◯ と完全一致したとき何が起きるかを、ランク別に測る（2026-08-19）。

## ユーザー観測（2026-08-19）

「2軸が WT ◎◯ と完全一致した場合、どちらか（両方）が4着以下になる。
  両方来ても人気決着が多い。完全一致の予想になった場合、再度判断が必要」

主張は2つに分かれる。**別々に検定する**:

  (a) 完全一致だと**軸が飛びやすい**     … 二軸的中率で測る
  (b) 完全一致だと**来ても安い**         … 払戻倍率・ガミ率・10倍以上の頻度で測る

⚠️ 看板穴埋め帯（ゲート未通過）で同じことを測った先行結果では **(a) は逆**だった
   （重なり2 の二軸的中 52.2% > 重なり1 の 35.9%）。(b) は成立していた
   （配当中央 1.09 vs 1.64）。[[keirin_marquee_fill_7car_axis_2026_08_19]]
   ここで測るのは**ゲートを通った本番ランク**なので母集団が違う。

母集団は `picks_history`（本番記録・買い目と配分は再構築が本番と同じ関数で作る）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_wt_mark_overlap_by_rank.py \
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


def axes_of(combo: str) -> list[int] | None:
    s = (combo or "").split(" ")[0]
    if s.startswith("三単"):
        n = [int(x) for x in re.findall(r"\d+", s)]
        return n[:2] if len(n) >= 3 else None
    if "-" in s:
        head = s.split("-", 1)[0]
        a = [int(x) for x in re.split(r"=", head) if x.strip().isdigit()]
        return a[:2] if len(a) == 2 else None
    return None


def load(d1, d2, ranks):
    q_ranks = ",".join("?" * len(ranks))
    with get_connection() as conn:
        cur = conn.execute(
            f"SELECT split_part(race_key,'#',1) rk, race_date, rank, pred_combo, "
            f"       bet_amount, payout FROM picks_history "
            f"WHERE race_date BETWEEN ? AND ? AND bet_amount > 0 AND rank IN ({q_ranks})",
            (d1, d2, *ranks))
        picks = [dict(rk=a, date=b, rank=c, combo=d, bet=e, pay=f)
                 for a, b, c, d, e, f in cur.fetchall()]
        keys = sorted({p["rk"] for p in picks})
        fin, marks = defaultdict(dict), defaultdict(dict)
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            for rk, fn, fo, mk in conn.execute(
                "SELECT race_key, frame_no, finish_order, prediction_mark FROM wt_entries "
                "WHERE race_key IN (%s)" % ",".join("?" * len(ch)), ch).fetchall():
                if fo:
                    fin[rk][int(fn)] = int(fo)
                if mk:
                    marks[rk][int(mk)] = int(fn)
    return picks, fin, marks


class Acc:
    def __init__(self):
        self.n = self.bet = self.pay = self.hit = self.disp = self.two = self.x10 = 0
        self.ratios = []
        self.flags = {}

    def add(self, rk, bet, pay, two):
        self.n += 1; self.bet += bet; self.pay += pay; self.two += int(two)
        self.flags[rk] = (int(two), int(pay > 0), int(pay >= bet and pay > 0))
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


HEAD = (f"  {'':16}{'R':>7}{'二軸的中%':>9}{'素の的中%':>9}{'表示的中%':>10}"
        f"{'ROI%':>8}{'倍率中央':>9}{'10倍+%':>9}")


def diff_ci(a: Acc, b: Acc, idx: int, n_iter=3000, seed=41):
    """flags[idx] の率の差（b − a）の 95%CI（独立2群 bootstrap）。"""
    ka, kb = list(a.flags), list(b.flags)
    if not ka or not kb:
        return 0, 0
    rnd = random.Random(seed); d = []
    for _ in range(n_iter):
        sa = [a.flags[ka[rnd.randrange(len(ka))]][idx] for _ in ka]
        sb = [b.flags[kb[rnd.randrange(len(kb))]][idx] for _ in kb]
        d.append(100*sum(sb)/len(sb) - 100*sum(sa)/len(sa))
    d.sort()
    return d[int(.025*len(d))], d[int(.975*len(d))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    a = ap.parse_args()
    RANKS = ("RANK_7C", "RANK_7S", "RANK_7M1", "RANK_7B", "RANK_9C")
    picks, fin, marks = load(a.d1, a.d2, RANKS)

    rows = []
    for p in picks:
        ax = axes_of(p["combo"])
        f, mk = fin.get(p["rk"]) or {}, marks.get(p["rk"]) or {}
        if not ax or len(f) < 3 or 1 not in mk or 2 not in mk:
            continue
        top3 = {n for n, o in f.items() if o <= 3}
        if len(top3) != 3:
            continue
        rows.append(dict(p, ax=ax, ov=len({ax[0], ax[1]} & {mk[1], mk[2]}),
                         two=set(ax) <= top3))
    print(f"\n分類できた推奨 {len(rows):,}件 [{a.d1}〜{a.d2}]")

    for rank in RANKS:
        sub = [r for r in rows if r["rank"] == rank]
        if len(sub) < 200:
            continue
        print(f"\n===== {rank.replace('RANK_','')} （{len(sub)}R）=====")
        print(HEAD)
        accs = {}
        for ov in (0, 1, 2):
            acc = Acc()
            for r in sub:
                if r["ov"] == ov:
                    acc.add(r["rk"], r["bet"], r["pay"], r["two"])
            if acc.n:
                accs[ov] = acc
                share = 100 * acc.n / len(sub)
                print(f"  {'◎◯重なり=' + str(ov):16}{acc.row()}   （{share:.1f}%）")
        if 2 in accs and 1 in accs:
            for idx, name in ((0, "二軸的中"), (1, "素の的中"), (2, "表示的中")):
                lo, hi = diff_ci(accs[1], accs[2], idx)
                base = sum(accs[1].flags[k][idx] for k in accs[1].flags) / accs[1].n
                new = sum(accs[2].flags[k][idx] for k in accs[2].flags) / accs[2].n
                print(f"    {name}: 重なり2 − 重なり1 = {100*(new-base):+5.1f}pt "
                      f"[{lo:+5.1f},{hi:+5.1f}]{'  有意' if lo>0 or hi<0 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
