"""地方競馬 race_results.place_odds の発走前最終オッズ補完。

現状: chihou.race_results.place_odds は HR(払戻) からの payoff のみで埋まっており、
1〜3着以外の馬では NULL が大半（約 4% しか入っていない）。

本スクリプトは chihou.odds_history (bet_type='place') の発走前最終スナップショットから
NULL 行を補完する。chihou.odds_history は 2026-04-07 以降に蓄積されているため、
それ以前のレースは対象外。

挙動:
  - 既存値は上書きしない（COALESCE 動作）
  - finish_position が IS NULL の行は対象外（出走取消等）
  - レース単位でバルク UPDATE。1日ずつ進める

使い方:
  cd backend
  .venv/bin/python scripts/backfill_chihou_place_odds.py --dry-run        # 件数のみ
  .venv/bin/python scripts/backfill_chihou_place_odds.py                  # 直近30日
  .venv/bin/python scripts/backfill_chihou_place_odds.py --start 20260407 --end 20260504
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import psycopg2
from psycopg2.extras import execute_batch

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def backfill_day(conn, day_str: str, dry_run: bool) -> tuple[int, int]:
    """1日分の race_results.place_odds を odds_history から補完する。

    Returns:
        (filled, skipped_no_odds): 埋めた行数 / オッズ未取得で埋められなかった行数
    """
    sql = """
        WITH target AS (
            SELECT rr.race_id, rr.horse_number
            FROM chihou.race_results rr
            JOIN chihou.races r ON r.id = rr.race_id
            WHERE r.date = %s
              AND rr.finish_position IS NOT NULL
              AND rr.place_odds IS NULL
        ),
        latest_place AS (
            SELECT DISTINCT ON (oh.race_id, oh.combination)
                oh.race_id,
                oh.combination,
                oh.odds,
                oh.fetched_at
            FROM chihou.odds_history oh
            JOIN chihou.races r ON r.id = oh.race_id
            WHERE r.date = %s AND oh.bet_type = 'place'
            ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC
        )
        SELECT
            t.race_id,
            t.horse_number,
            lp.odds
        FROM target t
        LEFT JOIN latest_place lp
          ON lp.race_id = t.race_id
         AND lp.combination = t.horse_number::text
    """
    with conn.cursor() as cur:
        cur.execute(sql, (day_str, day_str))
        rows = cur.fetchall()
    if not rows:
        return 0, 0

    fillable = [(r[2], r[0], r[1]) for r in rows if r[2] is not None]
    skipped = sum(1 for r in rows if r[2] is None)

    if not fillable:
        return 0, skipped

    if dry_run:
        return len(fillable), skipped

    update_sql = """
        UPDATE chihou.race_results
        SET place_odds = %s
        WHERE race_id = %s AND horse_number = %s AND place_odds IS NULL
    """
    with conn.cursor() as cur:
        execute_batch(cur, update_sql, fillable, page_size=500)
    conn.commit()
    return len(fillable), skipped


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="開始日 YYYYMMDD（既定: 30日前）")
    p.add_argument("--end", help="終了日 YYYYMMDD（既定: 今日）")
    p.add_argument("--dry-run", action="store_true", help="件数のみ表示し UPDATE しない")
    args = p.parse_args()

    today = date.today()
    if args.end:
        end = date.fromisoformat(f"{args.end[:4]}-{args.end[4:6]}-{args.end[6:8]}")
    else:
        end = today
    if args.start:
        start = date.fromisoformat(f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:8]}")
    else:
        start = today - timedelta(days=30)

    logger.info(
        "backfill range: %s 〜 %s  (dry_run=%s)",
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
        args.dry_run,
    )

    conn = psycopg2.connect(DSN)
    total_filled = 0
    total_skipped = 0
    try:
        for day in daterange(start, end):
            day_str = day.strftime("%Y%m%d")
            filled, skipped = backfill_day(conn, day_str, args.dry_run)
            if filled or skipped:
                logger.info(
                    "  %s  filled=%4d  skipped(no odds)=%4d", day_str, filled, skipped
                )
            total_filled += filled
            total_skipped += skipped
    finally:
        conn.close()

    action = "would fill" if args.dry_run else "filled"
    logger.info(
        "DONE: %s %d rows total  (skipped %d rows: odds_history に place 記録なし)",
        action,
        total_filled,
        total_skipped,
    )


if __name__ == "__main__":
    main()
