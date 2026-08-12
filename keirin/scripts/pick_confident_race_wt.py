#!/usr/bin/env python3
"""勝負アイコン「自信あり」を付ける1レースを選ぶ（2026-08-13 新設）。

netkeirin の「自信あり」は **1日に1つしか付けられない**。従来は 7SS の入稿すべてに
付けており、7SS が複数出た日は**先に入稿したものが取っていた**（選定ではなかった）。

ユーザー決定（2026-08-13）:
**朝の時点で当日全レースを見て、期待値が最も高い1レースだけに付ける。**

## 期待値

    EV = Σ(的中確率 × 賭け金 × オッズ) ÷ 総賭け金

- オッズは**予測オッズ**（`src.odds_prediction`）。朝の板は夜開催で 63.4% が
  未確定なので、板で比べると**朝に開催がある場だけが有利**になる。
  終日を同じ土俵に載せるために予測で統一する。
- 的中確率は Plackett-Luce の三連複確率。
- **三連複の買い目だけ**が対象（三連単は着順つきでこの確率モデルに載らない）。

計算の実体は `src/confident_pick.py`。

## いつ走らせるか

朝の日次バッチ（`daily_picks_wt.sh`）の**入稿のあと**に1回。
昼・夕の波では走らせない（当日2回目を選ぶと1日1件が壊れる）。

## 冪等性

実行のたびに **その日を全部 false にしてから1件だけ true** にする。
途中で落ちてもやり直せる。同値のときは race_key → rank_key で決めるので
何度走らせても同じ結果になる。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/pick_confident_race_wt.py [YYYY-MM-DD] [--dry-run]

DB は netkeirin_submissions の is_confident / confident_ev のみ更新する。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.confident_pick import pick_best, race_expected_value  # noqa: E402
from src.database import get_connection  # noqa: E402

# 取消済みは対象外（人が落としたものに自信アイコンを置かない）。
_ALIVE = "COALESCE(status, 'submitted') <> 'deleted'"


def _load_alive(date: str) -> list[dict]:
    ymd = date.replace("-", "")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, rank_key, venue_name, race_no, bet_detail "
            f"FROM netkeirin_submissions WHERE race_key LIKE ? AND {_ALIVE} "
            "ORDER BY race_key, rank_key",
            (f"{ymd}%",),
        ).fetchall()
    return [dict(r) for r in rows]


def pick(date: str, dry_run: bool = False) -> tuple[str, str] | None:
    """その日の「自信あり」を1件決めて記録する。決められなければ None。"""
    rows = _load_alive(date)
    if not rows:
        print(f"[confident] {date}: 生きている入稿がありません", flush=True)
        return None

    scored: list[tuple[str, str, float | None]] = []
    for r in rows:
        ev = race_expected_value(r["race_key"], r.get("bet_detail"))
        scored.append((r["race_key"], r["rank_key"], ev))

    usable = [(rk, rank, ev) for rk, rank, ev in scored if ev is not None]
    print(f"[confident] {date}: 対象 {len(rows)}件 / EV算出 {len(usable)}件", flush=True)
    label = {(r["race_key"], r["rank_key"]):
             f"{r['venue_name']}{r['race_no']}R({r['rank_key']})" for r in rows}
    for rk, rank, ev in sorted(usable, key=lambda t: -t[2])[:10]:
        print(f"    EV={ev:.3f}  {label.get((rk, rank), rk)}", flush=True)

    best = pick_best(scored)
    if best is None:
        # 🔴 **黙って終わらない。** 全件 EV なしは予測モデル未配備などの異常。
        print(f"[confident] {date}: EV を出せる入稿が1件も無く、自信アイコンは付けません",
              flush=True)
        return None
    race_key, rank_key = best
    print(f"[confident] {date}: 自信あり → {label.get(best, race_key)}", flush=True)
    if dry_run:
        print("[confident] dry-run のため DB は更新しません", flush=True)
        return best

    ymd = date.replace("-", "")
    with get_connection() as conn:
        # 🔴 **先に当日を全部 false にする**。1日1件はこの2文で担保している。
        conn.execute(
            "UPDATE netkeirin_submissions SET is_confident = FALSE "
            "WHERE race_key LIKE ?", (f"{ymd}%",))
        conn.execute(
            "UPDATE netkeirin_submissions SET is_confident = TRUE "
            "WHERE race_key = ? AND rank_key = ?", (race_key, rank_key))
        # 選定に使った EV を全件に残す。**確認画面はこの値を出す**ので、
        # 「なぜこのレースが選ばれたか」を後から読める。
        for rk, rank, ev in scored:
            conn.execute(
                "UPDATE netkeirin_submissions SET confident_ev = ? "
                "WHERE race_key = ? AND rank_key = ?", (ev, rk, rank))
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    pick(args.date, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
