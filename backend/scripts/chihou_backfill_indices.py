"""地方競馬指数バックフィルスクリプト

2025-01-01 以降の全レースに対して chihou 指数を算出・保存する。

使い方:
    cd backend
    uv run python scripts/chihou_backfill_indices.py
    uv run python scripts/chihou_backfill_indices.py --from-date 20250101
    uv run python scripts/chihou_backfill_indices.py --from-date 20250601 --batch-size 50
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings
from src.db.chihou_models import ChihouRace
from src.indices.chihou_calculator import BANEI_COURSE_CODE, ChihouIndexCalculator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def backfill(from_date: str, batch_size: int, to_date: str | None = None) -> None:
    """指定期間の全レースを指数算出する。

    Args:
        from_date: 開始日（YYYYMMDD）
        batch_size: 一度にコミットするレース数
        to_date: 終了日（YYYYMMDD, 省略時は全期間）
    """
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]

    # 対象レース一覧を取得（成績が入っているレースのみ = 過去レース）
    async with async_session() as db:
        cond = ChihouRace.date >= from_date
        if to_date:
            cond = and_(cond, ChihouRace.date < to_date)
        rows = await db.execute(
            select(ChihouRace.id, ChihouRace.date, ChihouRace.course_name, ChihouRace.race_number)
            .where(cond)
            .where(ChihouRace.course != BANEI_COURSE_CODE)
            .order_by(ChihouRace.date, ChihouRace.id)
        )
        races = rows.fetchall()

    period = f"{from_date}〜{to_date}" if to_date else f"{from_date}〜"
    logger.info("対象レース数: %d（%s）", len(races), period)

    saved_total  = 0
    error_total  = 0
    start_time   = time.time()

    async with async_session() as db:
        calc = ChihouIndexCalculator(db)

        for i, (race_id, date, course_name, race_num) in enumerate(races, 1):
            try:
                stats = await calc.calculate_and_save(race_id)
                saved_total += stats.get("saved", 0)
                error_total += stats.get("errors", 0)

                if stats.get("saved", 0) > 0:
                    logger.info(
                        "[%d/%d] %s %s %dR → saved=%d",
                        i, len(races), date, course_name, race_num, stats["saved"]
                    )

                if i % batch_size == 0:
                    await db.commit()
                    elapsed = time.time() - start_time
                    logger.info(
                        "  --- commit %d/%d (%.1fs elapsed, saved=%d) ---",
                        i, len(races), elapsed, saved_total
                    )

            except Exception as e:
                logger.error("race_id=%d でエラー: %s", race_id, e, exc_info=True)
                error_total += 1
                await db.rollback()

        await db.commit()

    elapsed = time.time() - start_time
    logger.info(
        "バックフィル完了: レース=%d, 指数保存=%d, エラー=%d, 経過時間=%.1fs",
        len(races), saved_total, error_total, elapsed
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="地方競馬指数バックフィル")
    parser.add_argument(
        "--from-date",
        default="20250101",
        help="開始日（YYYYMMDD, デフォルト: 20250101）",
    )
    parser.add_argument(
        "--to-date",
        default=None,
        help="終了日（YYYYMMDD, 省略時は全期間）。並列バックフィル用。",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="コミット単位のレース数（デフォルト: 100）",
    )
    args = parser.parse_args()

    asyncio.run(backfill(args.from_date, args.batch_size, args.to_date))


if __name__ == "__main__":
    main()
