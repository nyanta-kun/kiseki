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

終了コード: 取得できたら 0（**ピック 0 件でも 0**）。取得自体に失敗したら例外で落ちる。
    月曜はピックが出ないのが正常なので、0 件を異常扱いにしていない。
    「開催日なのに 0 件」は check_scrape_supply.py が中央開催日を見てから判定する。
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
from src.utils.cron_run import record  # noqa: E402
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
    with record("scrape_anagusa") as run, SyncSessionLocal() as session:
        records = anagusa.scrape(
            session,
            target,
            settings.sarabure_user,
            settings.sarabure_pass,
            dry_run=args.dry_run,
        )
        run.summary = f"ピック{len(records)}件"
    logging.info("=== 穴ぐさ取得処理 終了 (%d 件) ===", len(records))

    # 🔴 0 件を異常扱いにしない。
    #
    #   このジョブは土日月に走るが、**月曜はピックが出ない**（2026-09-06 実測:
    #   直近 60 日で穴ぐさが入っている日は土 9 日 / 日 9 日のみ、月曜は 0 日）。
    #   0 件で失敗を返すと毎週月曜に必ずエラーが出て、「いつも赤い監視」になる。
    #
    #   「開催日なのに 0 件」の判定は check_scrape_supply.py の
    #   `check_anagusa_presence` が持っている。あちらは **中央開催日かどうか**を
    #   見てから 0 件を WARN にしており、条件が正しい。ここで雑に重複させない。
    #
    #   取得そのものの失敗（ログイン失効・HTML 構造変化）は scrape() が例外を
    #   投げるので、この関数まで到達しない。
    return 0


if __name__ == "__main__":
    sys.exit(main())
