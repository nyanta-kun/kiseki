#!/usr/bin/env python3
"""POG 指名馬の出走想定・枠順確定を Discord へ通知する（CLI）。

sekito の 2 ジョブの移設先:

    id=95  bin/notify/pog-weekly-entries  `30 19 * * 3`  → --mode entry --weekend
    id=94  bin/notify/pog --barrier       `0 13 * * *`   → --mode barrier --tomorrow

中身は `src/services/pog_notify.py`。

## それぞれ何を見るか

    entry    keiba.projected_entries（出走想定。確定出馬表が出る前の水曜に使う）
    barrier  keiba.race_entries / chihou.race_entries（枠順が入ったもの）

移設元の出走想定も既に `keiba.projected_entries` を直読みしていた。
枠順の方は `sekito.v_entries` 経由で**馬名**突合だったので、
`netkeiba_horse_id` での直読みに変えた（`keiba.pog_picks` が持っているキー）。

## 🔴 その日ぶんを 1 通にまとめる

重複判定も日付単位。**一度送った後に頭数が増えても送り直さない**
（移設元と同じ。送り直すと同じ内容が何度も届く）。

## 使い方

    # 週末（土日月）の出走想定 — 水曜の夜に回す
    ... scripts/notify_pog_entries.py --mode entry --weekend

    # 翌日の枠順 — 毎日 13:00
    ... scripts/notify_pog_entries.py --mode barrier --tomorrow

    # 日付を指定 / 送らずに対象だけ見る
    ... scripts/notify_pog_entries.py --mode entry --date 2026-09-12 --dry-run

終了コード: 0 = 正常（送信 0 件も正常）。1 = 送信に失敗したものがある。
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402
from src.services.pog_notify import notify_day  # noqa: E402
from src.utils.cron_run import RunRecord, record  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def next_weekend(today: date) -> list[date]:
    """次の土・日・月を返す。

    移設元と同じ式。**今日が土曜なら翌週の土曜**を指す（当日ぶんを今さら
    「出走想定」として送らないため）。
    """
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)
    return [saturday, saturday + timedelta(days=1), saturday + timedelta(days=2)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="POG の出走想定・枠順確定を Discord へ通知する"
    )
    parser.add_argument("--mode", choices=("entry", "barrier"), required=True,
                        help="entry=出走想定 / barrier=枠順確定")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--weekend", action="store_true", help="次の土日月を対象にする")
    group.add_argument("--tomorrow", action="store_true", help="翌日を対象にする")
    group.add_argument("-d", "--date", help="対象日 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="送らずに対象だけ出す")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    today = datetime.now(JST).date()
    if args.date:
        days = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    elif args.weekend:
        days = next_weekend(today)
    elif args.tomorrow:
        days = [today + timedelta(days=1)]
    else:
        days = [today]

    # 🔴 `--dry-run` は cron_runs に記録しない。
    ctx = (
        nullcontext(RunRecord(job_name="(記録しない)"))
        if args.dry_run
        else record(f"notify_pog_{args.mode}")
    )
    sent = candidates = failed = 0
    with ctx as run, SyncSessionLocal() as session:
        for day in days:
            r = notify_day(session, day, args.mode, dry_run=args.dry_run)
            logging.info("%s (%s): 対象%d 送信%d 送信済み%d 失敗%d",
                         day, args.mode, r.candidates, r.sent, r.already, r.failed)
            sent += r.sent
            candidates += r.candidates
            failed += r.failed
        run.summary = (
            f"{args.mode} {days[0]}〜{days[-1]} "
            f"対象{candidates} 送信{sent} 失敗{failed}"
        )

    logging.info("=== %s: 対象%d 送信%d 失敗%d ===", args.mode, candidates, sent, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
