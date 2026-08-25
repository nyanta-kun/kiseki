#!/usr/bin/env python3
"""**実際に売った商品**の成績を出す（2026-08-15 新設）。

picks_history（ペーパー成績）ではなく `netkeirin_submissions` + `bet_detail` だけを
情報源にする。netkeirin は1レース1商品なので二重計上が構造的に起こらない。
背景と使い分けは `src/sold_performance.py` の docstring を参照。

    PYTHONPATH=. .venv/bin/python scripts/sold_performance_report.py
    PYTHONPATH=. .venv/bin/python scripts/sold_performance_report.py --start 2026-08-01
    PYTHONPATH=. .venv/bin/python scripts/sold_performance_report.py --by origin

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.sold_performance import (  # noqa: E402
    build_sold_races, group_by, summarize, winning_combo_labels,
)

#: `bet_detail` の保存開始日。これより前は買い目も金額も残っていない。
BET_DETAIL_SINCE = "2026-08-07"


def _fetch(start: str, end: str) -> tuple[list[dict], dict, dict]:
    with get_connection() as c:
        subs = [dict(r) for r in c.execute(
            "SELECT ns.race_key, ns.rank_key, ns.origin, ns.bet_detail, wr.race_date "
            "FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            # 🔴 取消済み（論理削除）は商品ではない
            "WHERE ns.deleted_at IS NULL AND wr.race_date BETWEEN ? AND ? "
            "ORDER BY wr.race_date, ns.race_no", (start, end))]
        keys = sorted({s["race_key"] for s in subs})
        if not keys:
            return subs, {}, {}
        fins: dict[str, list[tuple[int, int]]] = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 "WHERE race_key IN (%s) AND finish_order BETWEEN 1 AND 3"
                 % ",".join("?" * len(chunk)))
            for r in c.execute(q, chunk):
                d = dict(r)
                fins.setdefault(d["race_key"], []).append(
                    (int(d["finish_order"]), int(d["frame_no"])))
    # `(着順, 車番)` のまま渡す。⚠️ 車番だけに畳むと**同着が潰れる**。
    finishes = {k: sorted(v) for k, v in fins.items()}

    # 確定配当（100円あたり）。`bet_detail.odds` は入稿時点の値なので払戻には使わない。
    raw = _load_payouts_wt(keys)
    payouts: dict[str, dict[str, int]] = {}
    for rk, rows in finishes.items():
        pm = raw.get(rk, {})
        m: dict[str, int] = {}
        for label in winning_combo_labels(rows):
            if "=" in label:                       # 三連複（順不同）
                got = pm.get(("trio", frozenset(int(x) for x in label.split("="))))
            else:                                  # 三連単（着順）
                got = pm.get(("trifecta", tuple(int(x) for x in label.split("-"))))
            if got:
                m[label] = int(got)
        payouts[rk] = m
    return subs, finishes, payouts


def _line(label: str, s) -> str:
    def pct(v):
        return f"{v:.1%}" if v is not None else "—"

    return (f"{label:<14}{s.n_races:>5}{pct(s.hit_rate):>9}{pct(s.net_hit_rate):>10}"
            f"{s.bet:>11,}{s.payout:>11,}{pct(s.roi):>8}{pct(s.gami_rate):>8}"
            f"{(s.median_payout or 0):>11,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BET_DETAIL_SINCE)
    ap.add_argument("--end", default="2100-01-01")
    ap.add_argument("--by", default="rank_key",
                    choices=("rank_key", "race_date", "origin"))
    args = ap.parse_args()

    if args.start < BET_DETAIL_SINCE:
        print(f"⚠️ bet_detail の保存は {BET_DETAIL_SINCE} 開始です。"
              f"それ以前は買い目も金額も残っていないため集計できません。", file=sys.stderr)

    subs, finishes, payouts = _fetch(args.start, args.end)
    races, skipped = build_sold_races(subs, finishes, payouts)
    total = summarize(races, n_no_detail=skipped)

    print(f"=== 実際に売った商品の成績  {args.start} 〜 {args.end} ===")
    print("※ 情報源は netkeirin_submissions + bet_detail のみ（ペーパー行は混ぜない）")
    print("※ 実質的中＝払戻>=賭け金。**netkeirin の表示的中率はこちら**\n")
    head = (f"{'':<14}{'R数':>5}{'素の的中':>9}{'実質的中':>10}"
            f"{'投資':>11}{'払戻':>11}{'ROI':>8}{'ガミ率':>8}{'的中時中央':>11}")
    print(head)
    print(_line("合計", total))
    print("-" * len(head))
    for label, s in group_by(races, args.by).items():
        print(_line(label, s))

    if skipped:
        print(f"\n⚠️ 採点できず集計から外した入稿: {skipped} 件"
              f"（bet_detail が無い／結果が未確定／未知の券種）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
