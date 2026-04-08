"""地方競馬推奨生成スクリプト（毎日10:00 cron）

使い方:
    python scripts/calculate_chihou_recommendations.py           # 今日分
    python scripts/calculate_chihou_recommendations.py 20260408  # 指定日

crontab設定例（VPS）:
    # 毎日10:00 JST (01:00 UTC)
    0 1 * * * cd /app && .venv/bin/python scripts/calculate_chihou_recommendations.py >> /app/logs/chihou_recommendations.log 2>&1
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
from src.services.chihou_recommender import generate_chihou_recommendations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_recommendations")

JST = timezone(timedelta(hours=9))


async def run(date: str) -> None:
    """指定日の地方競馬推奨を生成する。"""
    async with AsyncSessionLocal() as db:
        recs = await generate_chihou_recommendations(db, date)
        logger.info("生成完了: %d 件", len(recs))
        for r in recs:
            logger.info(
                "  rank=%d race_id=%d bet=%s confidence=%.2f",
                r.rank,
                r.race_id,
                r.bet_type,
                r.confidence,
            )


def main() -> None:
    """エントリポイント。"""
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(JST).strftime("%Y%m%d")
    asyncio.run(run(date))


if __name__ == "__main__":
    main()
