"""head_count NULL 修正スクリプト

races.head_count が NULL のレースを race_results の実績頭数で補完する。

優先順位:
  1. race_results の finish_position IS NOT NULL な馬数（実際に走った頭数）
  2. race_results が0件の場合は race_entries の登録頭数
  3. それでも取れない場合はそのまま NULL（ログに記録）

実行方法:
  uv run python scripts/fix_head_count.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import text

from src.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_head_count")


def run(dry_run: bool = False) -> None:
    with engine.begin() as conn:
        # 現状確認
        row = conn.execute(
            text("""
            SELECT
                COUNT(*) total,
                SUM(CASE WHEN head_count IS NULL THEN 1 ELSE 0 END) nulls
            FROM keiba.races
        """)
        ).fetchone()
        logger.info(f"修正前: 総{row[0]:,}レース / NULL={row[1]:,} ({row[1] / row[0] * 100:.1f}%)")

        if dry_run:
            # どれだけ埋められるか確認
            row2 = conn.execute(
                text("""
                SELECT COUNT(DISTINCT r.id)
                FROM keiba.races r
                JOIN keiba.race_results rr ON rr.race_id = r.id
                WHERE r.head_count IS NULL
                  AND rr.finish_position IS NOT NULL
            """)
            ).fetchone()
            row3 = conn.execute(
                text("""
                SELECT COUNT(DISTINCT r.id)
                FROM keiba.races r
                JOIN keiba.race_entries re ON re.race_id = r.id
                WHERE r.head_count IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM keiba.race_results rr
                    WHERE rr.race_id = r.id AND rr.finish_position IS NOT NULL
                  )
            """)
            ).fetchone()
            logger.info(f"[DRY-RUN] race_results で補完できる: {row2[0]:,} レース")
            logger.info(f"[DRY-RUN] race_entries で補完できる: {row3[0]:,} レース")
            return

        # Step1: race_results の実走頭数で更新
        result1 = conn.execute(
            text("""
            UPDATE keiba.races r
            SET head_count = sub.cnt
            FROM (
                SELECT race_id, COUNT(*) cnt
                FROM keiba.race_results
                WHERE finish_position IS NOT NULL
                GROUP BY race_id
            ) sub
            WHERE r.id = sub.race_id
              AND r.head_count IS NULL
        """)
        )
        logger.info(f"race_results から補完: {result1.rowcount:,} レース")

        # Step2: race_entries の登録頭数で残りを更新
        result2 = conn.execute(
            text("""
            UPDATE keiba.races r
            SET head_count = sub.cnt
            FROM (
                SELECT race_id, COUNT(*) cnt
                FROM keiba.race_entries
                GROUP BY race_id
            ) sub
            WHERE r.id = sub.race_id
              AND r.head_count IS NULL
        """)
        )
        logger.info(f"race_entries から補完: {result2.rowcount:,} レース")

        # 修正後の状況
        row = conn.execute(
            text("""
            SELECT
                COUNT(*) total,
                SUM(CASE WHEN head_count IS NULL THEN 1 ELSE 0 END) nulls
            FROM keiba.races
        """)
        ).fetchone()
        remaining = row[1]
        logger.info(
            f"修正後: 総{row[0]:,}レース / NULL={remaining:,} ({remaining / row[0] * 100:.1f}%)"
        )

        if remaining > 0:
            # 残りのNULLを確認（エントリーも結果もないレース）
            rows = conn.execute(
                text("""
                SELECT date, course_name, race_number
                FROM keiba.races
                WHERE head_count IS NULL
                ORDER BY date DESC
                LIMIT 10
            """)
            ).fetchall()
            logger.info("残りNULLサンプル（最新10件）:")
            for r in rows:
                logger.info(f"  {r[0]} {r[1]} R{r[2]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="races.head_count NULL 修正")
    parser.add_argument("--dry-run", action="store_true", help="実際には更新せず確認のみ")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
