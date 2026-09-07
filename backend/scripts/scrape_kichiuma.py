#!/usr/bin/env python3
"""吉馬の SP 能力値を取得して `sekito.kichiuma` へ入れる（CLI）。

sekito の `bin/scrape/kichiuma`（scripts_schedules id=22 `30 0 * * *` と
id=92 `30 6 * * *` の 2 本）の移設先。中身は `src/scrapers/kichiuma.py`。

⚠️ 2 本ある理由（移設後もそのまま 2 本必要）:
    00:30 の回は sekito の地方供給同期（06:05）より前だったため、佐賀・門別など
    朝に確定する地方の出走表が間に合わず取りこぼしていた。06:30 の回はその補完。
    **移設後は供給が `chihou.races` 直読みになるので事情が変わる可能性がある**が、
    UmaConn 側の取り込み時刻に依存するため、まずは 2 本のまま移して実測する。
    取得済みは `should_fetch` でスキップされるので二重取得にはならない。

使い方:
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python scripts/scrape_kichiuma.py

    # 日付・場・レースを絞る / DB へ書かない / 取得済みも取り直す
    ... scripts/scrape_kichiuma.py --date 2026-09-06 --course JTOK,JNKY --race 11,12
    ... scripts/scrape_kichiuma.py --dry-run
    ... scripts/scrape_kichiuma.py --force

    # 中央だけ / 地方だけ
    ... scripts/scrape_kichiuma.py --only jra
    ... scripts/scrape_kichiuma.py --only nar

終了コード: 取得できたか既に取得済みなら 0。対象が有ったのに 1 件も片付かなければ 1。
    ⚠️ **スキップは異常ではない**。06:30 の回は 00:30 で取れたぶんが
    `should_fetch` に弾かれるので、正常な日ほどスキップだらけになる。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402
from src.utils.cron_run import record  # noqa: E402
from src.scrapers import kichiuma  # noqa: E402
from src.scrapers.targets import target_races  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    parser = argparse.ArgumentParser(description="吉馬の SP 能力値を取得する")
    parser.add_argument("-d", "--date", help="取得日 (YYYY-MM-DD)。既定は当日")
    parser.add_argument("-c", "--course", help="場コード（sekito 4文字・カンマ区切り）")
    parser.add_argument("-n", "--race", help="レース番号（カンマ区切り）")
    parser.add_argument("--only", choices=("jra", "nar"), help="中央のみ / 地方のみ")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書かない")
    parser.add_argument("--force", action="store_true", help="取得済みでも取り直す")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    target = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(JST).date()
    )
    courses = [c.strip() for c in args.course.split(",")] if args.course else None
    races = [int(r) for r in args.race.split(",") if r.strip()] if args.race else None

    logging.info("=== 吉馬取得処理 開始 (date=%s) ===", target)
    with record("scrape_kichiuma") as run, SyncSessionLocal() as session:
        targets = target_races(
            session,
            target,
            include_jra=args.only != "nar",
            include_nar=args.only != "jra",
            course_codes=courses,
            race_nos=races,
        )
        logging.info("対象レース: %d 件", len(targets))
        result = kichiuma.scrape(
            session, targets, dry_run=args.dry_run, force=args.force
        )
        run.summary = (f"対象{len(targets)} 成功{result.success}"
                       f" スキップ{result.skipped} エラー{result.errors}")
    logging.info(
        "=== 吉馬取得処理 終了 (成功 %d / スキップ %d / エラー %d) ===",
        result.success, result.skipped, result.errors,
    )

    if not targets:
        return 0
    # 🔴 スキップは異常ではない。1 日 2 回走らせる運用なので、2 回目は
    # ほぼ全件が `should_fetch` に弾かれる（それが正常）。ここを
    # 「成功 0 件なら失敗」にすると、取りこぼしが無い日ほど毎回 異常終了する。
    return 0 if result.handled else 1


if __name__ == "__main__":
    sys.exit(main())
