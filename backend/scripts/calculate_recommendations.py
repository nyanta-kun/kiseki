"""推奨レース・馬券生成スクリプト

毎朝8:00のcronから実行。当日のオッズ・指数をClaude APIに渡して推奨を生成・保存する。

使用例:
    uv run python scripts/calculate_recommendations.py           # 今日分
    uv run python scripts/calculate_recommendations.py 20260405  # 指定日
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

# scripts/ から src/ を参照できるようにパス追加
sys.path.insert(0, "/app")

from src.db.session import AsyncSessionLocal
from src.services.recommender import generate_recommendations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if len(sys.argv) >= 2:
        date = sys.argv[1]
    else:
        date = datetime.now(tz=timezone.utc).strftime("%Y%m%d")

    logger.info("推奨生成開始: date=%s", date)

    async with AsyncSessionLocal() as session:
        recs = await generate_recommendations(session, date)

    if recs:
        logger.info("推奨生成完了: %d 件", len(recs))
        for rec in sorted(recs, key=lambda r: r.rank):
            logger.info(
                "  推奨%d: race_id=%d bet=%s confidence=%.2f",
                rec.rank,
                rec.race_id,
                rec.bet_type,
                rec.confidence,
            )
    else:
        logger.warning("推奨が生成されませんでした")


if __name__ == "__main__":
    asyncio.run(main())
