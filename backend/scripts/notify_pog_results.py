#!/usr/bin/env python3
"""POG 指名馬のレース結果を Discord へ通知する（CLI）。

sekito の `bin/notify/pog-result`（scripts_schedules id=93・`*/10 10-23 * * *`）の
移設先。中身は `src/services/pog_notify.py`。

## 🔴 10 分ごとに走る

「まだ通知していない着順」だけを送る。判定は `keiba.pog_notifications` で、
**一意インデックスが DB 側でも重複を弾く**（移設元は SELECT のみで、実行が
重なると二度送りうる）。

## 使い方

    # 当日ぶん（本番コンテナ）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/notify_pog_results.py

    # 日付を指定 / 送らずに対象だけ見る
    ... scripts/notify_pog_results.py --date 2026-09-06
    ... scripts/notify_pog_results.py --dry-run

終了コード: 0 = 正常（送信 0 件も正常）。1 = 送信に失敗したものがある。
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402
from src.services.pog_notify import notify_results  # noqa: E402
from src.utils.cron_run import RunRecord, record  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    parser = argparse.ArgumentParser(description="POG の結果を Discord へ通知する")
    parser.add_argument("-d", "--date", help="対象日 YYYY-MM-DD。既定は当日")
    parser.add_argument("--dry-run", action="store_true", help="送らずに対象だけ出す")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    day = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else datetime.now(JST).date()
    )

    # 🔴 `--dry-run` は cron_runs に記録しない。試し打ちが残ると監視から見て
    #    「ジョブが正常に走った」と区別がつかない。
    ctx = (
        nullcontext(RunRecord(job_name="(記録しない)"))
        if args.dry_run
        else record("notify_pog_results")
    )
    with ctx as run, SyncSessionLocal() as session:
        result = notify_results(session, day, dry_run=args.dry_run)
        run.summary = (
            f"{day} 対象{result.candidates} 送信{result.sent} "
            f"送信済み{result.already} 失敗{result.failed} グループ{result.groups}"
        )

    logging.info(
        "=== %s: 対象%d 送信%d 送信済み%d 失敗%d ===",
        day, result.candidates, result.sent, result.already, result.failed,
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
