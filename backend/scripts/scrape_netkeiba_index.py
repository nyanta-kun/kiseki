#!/usr/bin/env python3
"""netkeiba のタイム指数を取得して `sekito.netkeiba` へ入れる（CLI）。

sekito の `bin/scrape/netkeiba-index`（scripts_schedules id=63・`30 8 * * *`）の
移設先。中身は `src/scrapers/netkeiba/index_job.py`。

⚠️ **この CLI はまだ cron に載せない。** sekito 側の id=63 が生きているあいだは
   手動検証だけに使う。二重に走らせても `should_fetch` で片方がスキップされるので
   データは壊れないが、netkeiba への負荷が倍になる。

🔴 **所要時間**: 2026-09-06 実測で 79 レース / 24.8 分（194 リクエスト・7.7 秒/件）。
   ほとんどがレートリミッタの待ちで、速くする唯一の方法は取得項目を減らすこと。
   sekito 側は既定 10 分でタイムアウト kill されて 2026-05 以降ずっと地方が
   全滅していた（9/5 も 10.0 分で failed）。**cron に載せるときは打ち切らないこと。**

使い方:
    # 当日ぶん（本番コンテナ）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/scrape_netkeiba_index.py

    # 日付・場・レースを絞る / DB へ書かない / 取得済みも取り直す
    ... scripts/scrape_netkeiba_index.py --date 2026-09-06 --course JHSN --race 1,2 --dry-run
    ... scripts/scrape_netkeiba_index.py --only nar
    ... scripts/scrape_netkeiba_index.py --force

終了コード: 取得できたか既に取得済みなら 0。IP 制限で中断したら 2。
    対象が有ったのに 1 件も片付かなければ 1。
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

from src.config import settings  # noqa: E402
from src.db.session import SyncSessionLocal  # noqa: E402
from src.scrapers.netkeiba import index_job  # noqa: E402
from src.scrapers.targets import target_races  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    parser = argparse.ArgumentParser(description="netkeiba のタイム指数を取得する")
    parser.add_argument("-d", "--date", help="取得日 (YYYY-MM-DD)。既定は当日")
    parser.add_argument("-c", "--course", help="場コード（sekito 4文字・カンマ区切り）")
    parser.add_argument("-n", "--race", help="レース番号（カンマ区切り）")
    parser.add_argument("--only", choices=("jra", "nar"), help="中央のみ / 地方のみ")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書かない")
    parser.add_argument("--force", action="store_true", help="取得済みでも取り直す")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    if not settings.netkeiba_user_id or not settings.netkeiba_password:
        logging.error("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が設定されていません")
        return 2

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else datetime.now(JST).date()
    )
    courses = [c.strip() for c in args.course.split(",")] if args.course else None
    races = [int(r) for r in args.race.split(",") if r.strip()] if args.race else None

    logging.info("=== タイム指数取得 開始 (date=%s) ===", target_date)
    with SyncSessionLocal() as session:
        targets = target_races(
            session, target_date,
            include_jra=args.only != "nar",
            include_nar=args.only != "jra",
            course_codes=courses, race_nos=races,
        )
        logging.info("対象レース: %d 件", len(targets))
        result = index_job.scrape(
            session, targets,
            user_id=settings.netkeiba_user_id,
            password=settings.netkeiba_password,
            environment_id=settings.scraper_environment_id,
            dry_run=args.dry_run, force=args.force,
        )

    logging.info("=== タイム指数取得 終了 (成功 %d / スキップ %d / 無し %d / エラー %d) ===",
                 result.success, result.skipped, result.unavailable, result.errors)
    if result.failed_races:
        logging.warning("失敗したレース: %s", ", ".join(result.failed_races[:10]))

    if result.aborted_reason:
        return 2
    if not targets:
        return 0
    # スキップも「片付いた」に数える。取りこぼしが無い日ほどスキップだらけになる。
    return 0 if result.handled else 1


if __name__ == "__main__":
    sys.exit(main())
