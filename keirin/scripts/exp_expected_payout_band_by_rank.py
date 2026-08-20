#!/usr/bin/env python3
"""想定払戻(下限)の帯ごとの成績を**ランク別**に出す（2026-08-19）。

## なぜ

`MIN_EXPECTED_PAYOUT_7C = 1.0`（想定払戻が賭け金を割りうる 7C は入稿しない）は
**7C でしか測っていない**。「7S にも要るか」を同じ物差しで確認する。

    想定払戻(下限) = min_i (賭け金_i × 入稿時点の板オッズ_i) ÷ 予算

## 母集団の作り方

7S の軸選定は3ヘッド（`pred_bad` が必要）で DB から再現できないため、
**`picks_history` の実際の買い目**（`pred_combo` の軸と相手）から復元する。
配分は本番と同じ `stakes_for_combos`（朝の板つき）、採点は確定オッズ。

⚠️ 朝の板（`wt_odds_snapshot` の morning）は **2026-06-08 以降しか無い**。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_expected_payout_band_by_rank.py \
        --ranks RANK_7C,RANK_7S --from 2026-06-08 --to 2026-08-18
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
from src.rebuild_stakes import stakes_for_combos  # noqa: E402

BUDGET = 10_000
BANDS = [(0, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 3.0), (3.0, 1e9)]


def _parse(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def axes_legs(combo: str):
    """`7=3-1,2,4,5,6` → ([7,3], [1,2,4,5,6])。三連単・固定目は対象外。"""
    s = (combo or "").split(" ")[0]
    if s.startswith("三単") or "-" not in s:
        return None
    head, tail = s.split("-", 1)
    ax = [int(x) for x in re.split(r"=", head) if x.strip().isdigit()]
    legs = [int(x) for x in re.split(r",", tail) if x.strip().isdigit()]
    return (ax, legs) if len(ax) == 2 and legs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ranks", default="RANK_7C,RANK_7S")
    ap.add_argument("--from", dest="d1", default="2026-06-08")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    a = ap.parse_args()
    ranks = a.ranks.split(",")

    with get_connection() as conn:
        q = ",".join("?" * len(ranks))
        cur = conn.execute(
            f"SELECT split_part(race_key,'#',1) rk, race_date, rank, pred_combo "
            f"FROM picks_history WHERE race_date BETWEEN ? AND ? AND bet_amount > 0 "
            f"  AND rank IN ({q})", (a.d1, a.d2, *ranks))
        picks = [dict(rk=x[0], date=x[1], rank=x[2], combo=x[3]) for x in cur.fetchall()]
        keys = sorted({p["rk"] for p in picks})
        fin, p3, snap = defaultdict(dict), defaultdict(dict), defaultdict(dict)
        for i in range(0, len(keys), 700):
            ch = keys[i:i + 700]
            ph = ",".join("?" * len(ch))
            for rk, fn, pp, fo in conn.execute(
                f"SELECT race_key, frame_no, pred_top3_pct, finish_order "
                f"FROM wt_entries WHERE race_key IN ({ph})", ch).fetchall():
                if pp is not None:
                    p3[rk][int(fn)] = float(pp) / 100.0
                if fo:
                    fin[rk][int(fn)] = int(fo)
            for rk, cb, od in conn.execute(
                f"SELECT race_key, combination, odds_value FROM wt_odds "
                f"WHERE race_key IN ({ph}) AND bet_type='trio' AND odds_value>0",
                ch).fetchall():
                fin[rk].setdefault("_od", {})[frozenset(_parse(cb))] = float(od)
            for rk, cb, od in conn.execute(
                f"SELECT race_key, combination, odds_value FROM wt_odds_snapshot "
                f"WHERE race_key IN ({ph}) AND bet_type='trio' "
                f"  AND snapshot_type='morning' AND odds_value>0 AND odds_value<9000",
                ch).fetchall():
                snap[rk][frozenset(_parse(cb))] = float(od)

    for rank in ranks:
        rows = []
        for p in picks:
            if p["rank"] != rank:
                continue
            got = axes_legs(p["combo"])
            od_final = (fin.get(p["rk"]) or {}).get("_od") or {}
            board = snap.get(p["rk"]) or {}
            probs = p3.get(p["rk"]) or {}
            if not got or not od_final or not board or not probs:
                continue
            (a1, a2), legs = got
            combos = [frozenset({a1, a2, t}) for t in legs]
            if not all(c in od_final and c in board for c in combos):
                continue
            top3 = {n for n, o in (fin[p["rk"]]).items()
                    if isinstance(n, int) and o <= 3}
            if len(top3) != 3:
                continue
            try:
                st = stakes_for_combos(a1, a2, combos, probs, board=board, budget=BUDGET)
            except Exception:
                continue
            bet = sum(st.values())
            # 🔴 `stakes_for_combos` のキーは**目（frozenset）**であって車番ではない。
            exp_lo = min(board[c] * st[c] / bet for c in combos)
            win = frozenset(top3)
            pay = int(od_final[win] * st[win]) if win in st else 0
            rows.append(dict(date=p["date"], exp_lo=exp_lo, bet=bet, pay=pay,
                             hit=pay > 0, net=pay >= bet and pay > 0))
        if not rows:
            print(f"\n{rank}: 対象0件"); continue
        days = len({r["date"] for r in rows})
        print(f"\n===== {rank}（{len(rows)}R / {days}日）[{a.d1}〜{a.d2}] =====")
        print(f"  {'想定払戻(下限)':16}{'R':>6}{'割合%':>7}{'素の的中%':>10}"
              f"{'表示的中%':>11}{'ROI%':>8}{'倍率中央':>9}")
        for lo, hi in BANDS:
            b = [r for r in rows if lo <= r["exp_lo"] < hi]
            if not b:
                continue
            rat = [r["pay"] / r["bet"] for r in b if r["hit"]]
            lbl = f"{lo:.1f}〜{hi:.1f}" if hi < 1e8 else f"{lo:.1f}以上"
            print(f"  {lbl:16}{len(b):>6}{100*len(b)/len(rows):>7.1f}"
                  f"{100*sum(r['hit'] for r in b)/len(b):>10.1f}"
                  f"{100*sum(r['net'] for r in b)/len(b):>11.1f}"
                  f"{100*sum(r['pay'] for r in b)/sum(r['bet'] for r in b):>8.1f}"
                  f"{(statistics.median(rat) if rat else 0):>9.2f}")
        tot = len(rows)
        print(f"  {'（全体）':16}{tot:>6}{100:>7.1f}"
              f"{100*sum(r['hit'] for r in rows)/tot:>10.1f}"
              f"{100*sum(r['net'] for r in rows)/tot:>11.1f}"
              f"{100*sum(r['pay'] for r in rows)/sum(r['bet'] for r in rows):>8.1f}")
        keep = [r for r in rows if r["exp_lo"] >= 1.0]
        if keep and len(keep) < tot:
            print(f"  → 1.0未満を除外すると: 除外 {tot-len(keep)}R "
                  f"({100*(tot-len(keep))/tot:.1f}%) / 残る側 "
                  f"素の的中 {100*sum(r['hit'] for r in keep)/len(keep):.1f}% / "
                  f"表示的中 {100*sum(r['net'] for r in keep)/len(keep):.1f}% / "
                  f"ROI {100*sum(r['pay'] for r in keep)/sum(r['bet'] for r in keep):.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
