"""発走直前のオッズスナップショット（winticket・毎分 cron・2026-11-15 新設）

## 何をするか

各レースについて、発走 **10分前（t10）** と **7分前（t07）** の3連複・3連単オッズを
`wt_odds_snapshot` に1回ずつ残す。`snapshot_at` には実際に取った時刻（JST）を入れる。

## なぜ要るか

- `wt_odds` は15分ごとの `collect-wt` で上書きされ、いつの値かが残らない
- 定時の `h10`〜`h20` は2時間おきで、発走前最後の記録は中央値で発走77分前
  （2026-10-20〜11-08・1,511R の実測）

kaime-sim（ai-company の仮想運用。本物の購入はしない）は発走6分前の時点で判断する。
その時点のオッズは**後から作れない**ので、取り始めた日以降しか検証に使えない。

## 決まり

- 毎分起動する。対象が無ければ winticket にも DB の書き込みにも触れずに終わる
- 発走まで 7分超〜10分以内 → `t10`、0分超〜7分以内 → `t07`（それぞれ未取得なら）
- 🔴 **窓を逃した型は後から埋めない。** 10分前の回を取り逃して7分前に気づいたら
  `t07` だけを取る。7分前に取った値に `t10` の名前を付けると、名前が時点を表さなくなる
- 🔴 **発走時刻を過ぎたレースは撮らない**（締切間際の値が混ざり、型の意味が崩れる）
- 書き込みは `INSERT OR IGNORE`。同じ型を2回取っても最初の値が残る
- 券種は型ラボが売っている3連複・3連単だけ

使い方:
  PYTHONPATH=. .venv/bin/python3 scripts/snapshot_prestart_odds_wt.py [--dry-run]

VPS cron（毎分・8〜23時。最終レースは23時台発走）:
  * 8-23 * * * cd $KEIRIN_HOME && flock -n data/logs/snapshot_prestart.lock env PYTHONPATH=. .venv/bin/python3 scripts/snapshot_prestart_odds_wt.py >> data/logs/snapshot_prestart.log 2>&1

欠けは `check_odds_snapshot_health.py`（翌0:10）が拾う。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection
from src.scraper.winticket import WinticketScraper

logger = logging.getLogger(__name__)

_JST = timezone(timedelta(hours=9))

#: 型の名前と、その型を取る窓の上端（発走までの秒数）。下端は次の型の上端。
T10 = "t10"
T07 = "t07"
T10_MAX_SEC = 10 * 60
T07_MAX_SEC = 7 * 60
PRESTART_TYPES = (T10, T07)

#: 保存する券種（型ラボの本番の券種）。
TARGET_BET_TYPES = ("trio", "trifecta")
#: 順序あり券種の区切り（`snapshot_intraday_odds_wt.py` と同じ）。
_ORDERED_BET_TYPES = {"exacta", "trifecta"}


def due_type(seconds_to_start: float, have: set[str]) -> Optional[str]:
    """いま取るべき型を返す（取るものが無ければ None）。DB にも通信にも依存しない。

    seconds_to_start : 発走までの秒数（負なら発走済み）
    have             : そのレースで既に取ってある型
    """
    if seconds_to_start <= 0:
        return None
    if seconds_to_start <= T07_MAX_SEC:
        return None if T07 in have else T07
    if seconds_to_start <= T10_MAX_SEC:
        return None if T10 in have else T10
    return None


def odds_rows(race_key: str, odds_data: dict, snapshot_type: str,
              snapshot_at: str) -> list[tuple]:
    """`fetch_odds` の結果を `wt_odds_snapshot` の行へ直す（対象券種だけ）。"""
    rows = []
    for bet_type in TARGET_BET_TYPES:
        sep = "-" if bet_type in _ORDERED_BET_TYPES else "="
        for item in odds_data.get(bet_type) or []:
            combo = item.get("combination", "")
            if isinstance(combo, (list, tuple)):
                combo = sep.join(str(x) for x in combo)
            odds_val = item.get("odds_value")
            if combo and odds_val:
                rows.append((race_key, bet_type, combo, odds_val,
                             snapshot_type, snapshot_at))
    return rows


def _upcoming_races(now: datetime) -> list[tuple]:
    """発走まで 0〜10分のレース（中止を除く）。"""
    now_unix = int(now.timestamp())
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT race_key, venue_id, race_no, cup_id, day_index, start_at
            FROM wt_races
            WHERE race_date = ?
              AND cancel = 0
              AND start_at > ?
              AND start_at <= ?
            ORDER BY start_at
            """,
            (now.date().isoformat(), str(now_unix), str(now_unix + T10_MAX_SEC)),
        ).fetchall()
    return [tuple(r) for r in rows]


def _taken_types(race_keys: list[str]) -> dict[str, set[str]]:
    """レースごとに既に取ってある直前の型。"""
    if not race_keys:
        return {}
    ph = ",".join("?" * len(race_keys))
    tp = ",".join("?" * len(PRESTART_TYPES))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT race_key, snapshot_type FROM wt_odds_snapshot
            WHERE race_key IN ({ph}) AND snapshot_type IN ({tp})
            """,
            (*race_keys, *PRESTART_TYPES),
        ).fetchall()
    out: dict[str, set[str]] = {}
    for rk, st in rows:
        out.setdefault(rk, set()).add(st)
    return out


def run(now: Optional[datetime] = None, dry_run: bool = False,
        clock=None, scraper=None) -> dict[str, int]:
    """1回分を実行し、取った型ごとのレース数を返す。

    clock   : 現在時刻を返す関数（テスト用）。取得の直前ごとに時刻を測り直すため
    scraper : WinticketScraper 互換（テスト用）
    """
    clock = clock or (lambda: datetime.now(_JST))
    now = now or clock()
    races = _upcoming_races(now)
    if not races:
        return {}
    have = _taken_types([r[0] for r in races])

    done: dict[str, int] = {}
    for race_key, venue_id, race_no, cup_id, day_index, start_at in races:
        # 取得は1レース数秒かかるので、判定はその都度の時刻で行う
        t = clock()
        st = due_type(int(start_at) - t.timestamp(), have.get(race_key, set()))
        if st is None:
            continue
        if scraper is None:
            scraper = WinticketScraper(request_interval=1.0)
        try:
            odds_data = scraper.fetch_odds(venue_id, t.date().isoformat(),
                                           race_no, cup_id, day_index)
        except Exception as e:  # noqa: BLE001 - 1レースの失敗で他を止めない
            logger.warning("[prestart] %s fetch_odds 失敗: %s", race_key, e)
            continue
        taken_at = clock()
        # 取得に時間がかかって発走を過ぎたものは捨てる（締切後の値を混ぜない）
        if int(start_at) - taken_at.timestamp() <= 0:
            print(f"[prestart] {race_key} {st}: 取得中に発走時刻を過ぎたため捨てる")
            continue
        rows = odds_rows(race_key, odds_data or {}, st,
                         taken_at.isoformat(timespec="seconds"))
        if not rows:
            print(f"[prestart] {race_key} {st}: オッズが取れない")
            continue
        if not dry_run:
            with get_connection() as conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO wt_odds_snapshot
                        (race_key, bet_type, combination, odds_value,
                         snapshot_type, snapshot_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        have.setdefault(race_key, set()).add(st)
        done[st] = done.get(st, 0) + 1
        mins = (int(start_at) - taken_at.timestamp()) / 60
        print(f"[prestart] {taken_at:%H:%M:%S} {race_key} {st}"
              f"（発走 {mins:.1f}分前）{len(rows)}行{' [DRY RUN]' if dry_run else ''}")
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="発走直前のオッズスナップショット（t10 / t07）")
    ap.add_argument("--dry-run", action="store_true", help="DB へ書かずに件数だけ表示")
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
