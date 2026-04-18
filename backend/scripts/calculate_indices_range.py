"""日付範囲指定の一括指数算出スクリプト

指定した開始〜終了日の間で、レース・出馬表・成績データが揃っている
全開催日の指数を算出して calculated_indices テーブルへ保存する。

バックテスト準備用として使用。

最適化:
  - CompositeIndexCalculator を1インスタンスで全日付を処理
    → SireStatsCache（重い統計集計）をセッション全体で1回のみ実行
  - expunge_all() でORMオブジェクトのメモリを日次解放（キャッシュ辞書は維持）
  - --skip-existing で算出済みバージョンをスキップ（中断・再開対応）

使い方:
  python scripts/calculate_indices_range.py --start 20250101 --end 20260322
  python scripts/calculate_indices_range.py --start 20250101 --end 20260322 --skip-existing
  python scripts/calculate_indices_range.py --start 20250101 --end 20260322 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import text

from src.db.session import AsyncSessionLocal
from src.indices.composite import COMPOSITE_VERSION, CompositeIndexCalculator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("calculate_range")

_DATE_QUERY = text("""
SELECT DISTINCT r.date
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id = r.id
JOIN keiba.race_results rr ON rr.race_id = r.id
WHERE r.date BETWEEN :start_date AND :end_date
ORDER BY r.date DESC
""")

_DATE_QUERY_SKIP = text("""
SELECT DISTINCT r.date
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id = r.id
JOIN keiba.race_results rr ON rr.race_id = r.id
WHERE r.date BETWEEN :start_date AND :end_date
  AND NOT EXISTS (
    SELECT 1 FROM keiba.calculated_indices ci
    JOIN keiba.races r2 ON r2.id = ci.race_id
    WHERE r2.date = r.date
      AND ci.version = :version
    LIMIT 1
  )
ORDER BY r.date DESC
""")


async def get_target_dates(
    start_date: str,
    end_date: str,
    skip_existing: bool = False,
) -> list[str]:
    """出走表・成績の両方が存在する開催日一覧を返す。

    Args:
        start_date: 開始日 YYYYMMDD
        end_date: 終了日 YYYYMMDD
        skip_existing: True のとき算出済みバージョンの日付をスキップ
    """
    async with AsyncSessionLocal() as db:
        if skip_existing:
            result = await db.execute(
                _DATE_QUERY_SKIP,
                {"start_date": start_date, "end_date": end_date, "version": COMPOSITE_VERSION},
            )
        else:
            result = await db.execute(
                _DATE_QUERY,
                {"start_date": start_date, "end_date": end_date},
            )
        return [row[0] for row in result]


async def run(
    start_date: str,
    end_date: str,
    dry_run: bool = False,
    skip_existing: bool = False,
) -> None:
    """一括算出を実行する。

    単一セッション・単一 CompositeIndexCalculator で全日付を処理する。
    SireStatsCache（重い初期化）はセッション全体で1回のみ実行される。
    各日付の commit 後に expunge_all() でORMオブジェクトをメモリから解放する。
    """
    dates = await get_target_dates(start_date, end_date, skip_existing)
    skipped_msg = "（算出済みスキップ）" if skip_existing else ""
    logger.info(
        f"対象開催日: {len(dates)} 日 ({end_date}→{start_date} 新しい順){skipped_msg} "
        f"version={COMPOSITE_VERSION}"
    )

    if dry_run:
        for d in dates:
            print(d)
        return

    if not dates:
        logger.info("算出対象なし（--skip-existing により全日付スキップ済み）")
        return

    total_horses = 0
    errors = 0

    # ── 単一セッション・単一インスタンスで全日付を処理 ──
    # SireStatsCache は最初の calculate_batch 呼び出し時に1回だけ構築される
    async with AsyncSessionLocal() as db:
        calc = CompositeIndexCalculator(db)

        for i, date in enumerate(dates, 1):
            try:
                rows = await calc.calculate_batch_for_date(date)
                await db.commit()
                # ORMオブジェクトをメモリから解放（SireStatsCache 等の辞書キャッシュは保持）
                db.expunge_all()
                total_horses += len(rows)
                logger.info(
                    f"[{i:>4}/{len(dates)}] {date}: {len(rows):>4} 頭 (累計 {total_horses:,})"
                )
            except Exception as e:
                await db.rollback()
                db.expunge_all()
                errors += 1
                logger.error(f"[{i:>4}/{len(dates)}] {date}: エラー → {e}")

    logger.info(f"完了: {len(dates)} 日 / {total_horses:,} 頭分の指数を保存 (エラー: {errors} 日)")


def main() -> None:
    parser = argparse.ArgumentParser(description="日付範囲指数一括算出")
    parser.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    parser.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=f"v{COMPOSITE_VERSION} 算出済みの日付をスキップ（中断再開用）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="対象日付のみ表示（算出しない）",
    )
    args = parser.parse_args()
    asyncio.run(run(args.start, args.end, args.dry_run, args.skip_existing))


if __name__ == "__main__":
    main()
