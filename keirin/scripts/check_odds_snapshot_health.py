#!/usr/bin/env python3
"""オッズスナップショット（wt_odds_snapshot）の欠けを見張る（2026-10-07 新設・読み取りのみ）。

    python scripts/check_odds_snapshot_health.py            # 前日
    python scripts/check_odds_snapshot_health.py 2026-10-06
    python scripts/check_odds_snapshot_health.py --no-discord

## なぜ要るか

`wt_odds_snapshot` は誰にも見られていないまま2回欠けた。どちらも**後から埋められない**
（スナップショットは発走前にしか取れない）。

- 2026-06-17・06-19〜07-15: 全種類が欠けた（旧 Mac cron → VPS 移行期）
- `evening`（16:00）: 2026-09-22 から取れていない。#603 で `evening_picks_wt.sh` を
  退役させた副作用（取得は同スクリプトの中にあった）

## 見るもの

開催があるのに次のどれかが1件も無い、または morning の被覆が 90% 未満なら
Discord の `system` へ1通送る。

    morning / h10 / h12 / h14 / h16 / h18 / h20

- `evening` は**見ない**。退役済み（h16 に置き換えた。取得方法が別物なので同一視しない）
- `h16` は導入日より前の日は判定しない（`H16_FIRST_DAY`）
- morning の被覆 = 7車以上のレースのうち morning がある割合
  （朝バッチは7車未満を対象にしない）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: 毎日そろっているべき種類。`evening` は退役済みなので含めない。
EXPECTED_TYPES = ("morning", "h10", "h12", "h14", "h16", "h18", "h20")
#: h16 を判定し始める日（導入は 2026-10-07。当日は配備時刻次第で欠けうるので翌日から）。
H16_FIRST_DAY = "2026-10-08"
#: morning の被覆の下限（7車以上のレースに対する割合）。
MORNING_MIN_COVERAGE = 0.90
#: 朝バッチの対象にする最小車数。
MIN_ENTRIES = 7


def evaluate(day: str, n_races: int, n_big: int, morning_big: int,
             type_counts: dict[str, int]) -> list[str]:
    """欠けの一覧を返す（空なら正常）。DB にも通知にも依存しない純関数。

    n_races     : その日の開催レース数（中止を除く）。0 なら開催なしで判定しない
    n_big       : うち MIN_ENTRIES 車以上のレース数
    morning_big : n_big のうち morning があるレース数
    type_counts : snapshot_type -> その日のレースに付いた行数（0 や欠落は「無い」）
    """
    if n_races <= 0:
        return []
    problems: list[str] = []
    for t in EXPECTED_TYPES:
        if t == "h16" and day < H16_FIRST_DAY:
            continue
        if type_counts.get(t, 0) <= 0:
            problems.append(f"{t} が1件も無い")
    if n_big > 0:
        cov = morning_big / n_big
        if cov < MORNING_MIN_COVERAGE:
            problems.append(
                f"morning の被覆 {cov:.0%}（{morning_big}/{n_big}・{MIN_ENTRIES}車以上）"
                f"が {MORNING_MIN_COVERAGE:.0%} 未満")
    return problems


def build_message(day: str, problems: list[str]) -> str:
    """Discord 用の本文。"""
    body = "\n".join(f"・{p}" for p in problems)
    return (f"⚠️ **オッズスナップショットに欠け**（{day}）\n{body}\n"
            "スナップショットは発走前にしか取れず、後から埋められない。"
            "取得経路（VPS cron / intraday_results_wt.sh の h16）を確認すること。")


def collect(day: str) -> tuple[int, int, int, dict[str, int]]:
    """DB から (n_races, n_big, morning_big, type_counts) を集める（読み取りのみ）。"""
    from src.database import get_connection

    with get_connection() as conn:
        n_races, n_big = conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(CASE WHEN n_entries >= ? THEN 1 ELSE 0 END), 0)
            FROM wt_races WHERE race_date = ? AND cancel = 0
            """, (MIN_ENTRIES, day)).fetchone()
        rows = conn.execute(
            """
            SELECT s.snapshot_type, COUNT(*)
            FROM wt_odds_snapshot s JOIN wt_races r ON r.race_key = s.race_key
            WHERE r.race_date = ? AND r.cancel = 0
            GROUP BY s.snapshot_type
            """, (day,)).fetchall()
        morning_big = conn.execute(
            """
            SELECT COUNT(*) FROM wt_races r
            WHERE r.race_date = ? AND r.cancel = 0 AND r.n_entries >= ?
              AND EXISTS (SELECT 1 FROM wt_odds_snapshot s
                          WHERE s.race_key = r.race_key AND s.snapshot_type = 'morning')
            """, (day, MIN_ENTRIES)).fetchone()[0]
    return int(n_races), int(n_big), int(morning_big), {t: int(c) for t, c in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="wt_odds_snapshot の欠けを見張る")
    ap.add_argument("day", nargs="?", default=None, help="YYYY-MM-DD（既定: 前日）")
    ap.add_argument("--no-discord", action="store_true", help="標準出力だけ")
    args = ap.parse_args()
    day = args.day or (date.today() - timedelta(days=1)).isoformat()

    n_races, n_big, morning_big, counts = collect(day)
    problems = evaluate(day, n_races, n_big, morning_big, counts)
    if n_races <= 0:
        print(f"[snapshot_health] {day}: 開催なし")
        return 0
    if not problems:
        print(f"[snapshot_health] {day}: 正常（{n_races}R・morning {morning_big}/{n_big}）")
        return 0

    msg = build_message(day, problems)
    print(msg)
    if not args.no_discord:
        from src.notify.discord import send
        try:
            ok = send(msg, channel="system")
        except Exception as e:  # noqa: BLE001 - 通知の失敗で夜間チェーンを止めない
            print(f"[snapshot_health] Discord 送信で例外: {e}")
            ok = False
        if not ok:
            print("[snapshot_health] Discord への送信に失敗")
    return 1


if __name__ == "__main__":
    sys.exit(main())
