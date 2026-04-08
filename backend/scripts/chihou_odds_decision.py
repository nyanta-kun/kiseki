"""地方競馬 発走10分前オッズ判断スクリプト（毎分cron）

使い方:
    python scripts/chihou_odds_decision.py  # 現在時刻から8〜15分後に発走するレースを対象

crontab設定例（VPS）:
    # 毎分実行
    * * * * * cd /app && .venv/bin/python scripts/chihou_odds_decision.py >> /app/logs/chihou_odds_decision.log 2>&1
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from src.db.session import AsyncSessionLocal
from src.services.chihou_recommender import update_chihou_odds_decision

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_odds_decision")


async def run() -> None:
    """発走10分前の推奨にオッズ判断を更新する。"""
    async with AsyncSessionLocal() as db:
        count = await update_chihou_odds_decision(db)
        if count:
            logger.info("オッズ判断更新: %d 件", count)


if __name__ == "__main__":
    asyncio.run(run())
