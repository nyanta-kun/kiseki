"""推奨レース結果更新スクリプト

成績確定後（主に月曜）のcronから実行。直近N日分の推奨の的中・払戻を更新する。

使用例:
    uv run python scripts/update_recommendation_results.py           # 直近7日分
    uv run python scripts/update_recommendation_results.py 20260405  # 指定日
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

# scripts/ から src/ を参照できるようにパス追加
sys.path.insert(0, "/app")

from src.db.session import AsyncSessionLocal
from src.services.recommender import update_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if len(sys.argv) >= 2:
        # 指定日のみ更新
        dates = [sys.argv[1]]
    else:
        # 直近7日分を更新（成績確定分をカバー）
        today = datetime.now(tz=timezone.utc)
        dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]

    total_updated = 0
    async with AsyncSessionLocal() as session:
        for date in dates:
            count = await update_results(session, date)
            if count > 0:
                logger.info("結果更新: date=%s → %d 件", date, count)
                total_updated += count

    if total_updated > 0:
        logger.info("結果更新完了: 合計 %d 件", total_updated)
    else:
        logger.info("更新対象なし（成績未確定 or 推奨データなし）")


if __name__ == "__main__":
    asyncio.run(main())
