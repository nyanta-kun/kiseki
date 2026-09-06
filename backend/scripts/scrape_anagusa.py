#!/usr/bin/env python3
"""サラブレ「穴ぐさ」を取得して `sekito.anagusa` へ入れる（CLI）。

sekito の `bin/scrape/anagusa`（scripts_schedules id=21・`10 7 * * 6,0,1`）の移設先。
中身は `src/scrapers/anagusa.py`。ここは引数と実行ログだけを持つ。

使い方:
    # 当日ぶん（VPS・コンテナ内）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python scripts/scrape_anagusa.py

    # 日付を指定 / DB へ書かずに件数だけ見る
    ... scripts/scrape_anagusa.py --date 2026-09-06
    ... scripts/scrape_anagusa.py --date 2026-09-06 --dry-run

    # VPS cron（移設が済んだら sekito 側 id=21 を無効化して、こちらへ移す）
    10 7 * * 6,0,1 /home/ysuzuki/GitHub/kiseki/scripts/scrape_anagusa.sh \
      >> /home/ysuzuki/GitHub/kiseki/logs/scrape_anagusa.log 2>&1

🔴 `SARABURE_USER` / `SARABURE_PASS` が要る。移設時点では sekito 側の .env に
   しか無いので、kiseki の .env にも同じ値を置くこと（置かないと即座に失敗する）。

終了コード: 取得できたら 0、ピックが 1 件も取れなければ 1。
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
from src.scrapers import anagusa  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    parser = argparse.ArgumentParser(description="サラブレ（穴ぐさ）を取得する")
    parser.add_argument("-d", "--date", help="取得日 (YYYY-MM-DD)。既定は当日")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書かない")
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

    if not settings.sarabure_user or not settings.sarabure_pass:
        logging.error("SARABURE_USER / SARABURE_PASS が設定されていません（.env を確認）")
        return 2

    logging.info("=== 穴ぐさ取得処理 開始 (date=%s) ===", target)
    with SyncSessionLocal() as session:
        records = anagusa.scrape(
            session,
            target,
            settings.sarabure_user,
            settings.sarabure_pass,
            dry_run=args.dry_run,
        )
    logging.info("=== 穴ぐさ取得処理 終了 (%d 件) ===", len(records))

    # 穴ぐさは「開催日なのにピックが 0 件」が典型的な壊れ方（上流の停止・ログイン失効）。
    # check_scrape_supply.py も比率ではなく有無で見ている。ここでも 0 件は異常扱いにする。
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
