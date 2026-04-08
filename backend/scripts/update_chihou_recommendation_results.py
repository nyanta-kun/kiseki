"""地方競馬推奨結果更新スクリプト（レース後cron）

使い方:
    python scripts/update_chihou_recommendation_results.py           # 直近7日分
    python scripts/update_chihou_recommendation_results.py 20260408  # 指定日

crontab設定例（VPS）:
    # 毎日22:00 JST (13:00 UTC)
    0 13 * * * cd /app && .venv/bin/python scripts/update_chihou_recommendation_results.py >> /app/logs/chihou_update_results.log 2>&1
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from src.db.session import AsyncSessionLocal
from src.services.chihou_recommender import update_chihou_results

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_update_results")

JST = timezone(timedelta(hours=9))


async def run(dates: list[str]) -> None:
    """指定日リストの推奨結果を更新する。"""
    async with AsyncSessionLocal() as db:
        for date in dates:
            count = await update_chihou_results(db, date)
            if count:
                logger.info("%s: %d 件更新", date, count)


def main() -> None:
    """エントリポイント。"""
    if len(sys.argv) > 1:
        dates = [sys.argv[1]]
    else:
        today = datetime.now(JST)
        dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]
    asyncio.run(run(dates))


if __name__ == "__main__":
    main()
