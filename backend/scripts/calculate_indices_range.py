"""日付範囲指定の一括指数算出スクリプト

指定した開始〜終了日の間で、レース・出馬表・成績データが揃っている
全開催日の指数を算出して calculated_indices テーブルへ保存する。

バックテスト準備用として使用。

使い方:
  python scripts/calculate_indices_range.py --start 20240101 --end 20241231
  python scripts/calculate_indices_range.py --start 20240101 --end 20241231 --dry-run
"""

from __future__ import annotations

import argparse
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
from sqlalchemy.orm import Session

from src.db.session import engine
from src.indices.composite import CompositeIndexCalculator

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
ORDER BY r.date
""")


def get_target_dates(start_date: str, end_date: str) -> list[str]:
    """出走表・成績の両方が存在する開催日一覧を返す。"""
    with Session(engine) as db:
        result = db.execute(_DATE_QUERY, {"start_date": start_date, "end_date": end_date})
        return [row[0] for row in result]


def run(start_date: str, end_date: str, dry_run: bool = False) -> None:
    dates = get_target_dates(start_date, end_date)
    logger.info(f"対象開催日: {len(dates)} 日 ({start_date}〜{end_date})")

    if dry_run:
        for d in dates:
            print(d)
        return

    total_horses = 0
    for i, date in enumerate(dates, 1):
        try:
            with Session(engine) as db:
                calc = CompositeIndexCalculator(db)
                rows = calc.calculate_batch_for_date(date)
                db.commit()
            total_horses += len(rows)
            logger.info(f"[{i:>4}/{len(dates)}] {date}: {len(rows)} 頭 (累計 {total_horses:,})")
        except Exception as e:
            logger.error(f"[{i:>4}/{len(dates)}] {date}: エラー → {e}")

    logger.info(f"完了: {len(dates)} 日 / {total_horses:,} 頭分の指数を保存")


def main() -> None:
    parser = argparse.ArgumentParser(description="日付範囲指数一括算出")
    parser.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    parser.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true", help="対象日付のみ表示（算出しない）")
    args = parser.parse_args()
    run(args.start, args.end, args.dry_run)


if __name__ == "__main__":
    main()
