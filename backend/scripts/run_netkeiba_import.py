#!/usr/bin/env python3
"""netkeibaインポートを単体実行するスクリプト。

使い方:
    # 日付指定（推奨: 1日分の全レースを一括処理）
    python scripts/run_netkeiba_import.py --date 20251228

    # レースID指定（複数可）
    python scripts/run_netkeiba_import.py --race-id 35537 56060 56061
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from sqlalchemy.orm import Session

from src.config import settings
from src.importers.netkeiba_importer import import_for_date, import_previous_race_extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="netkeibaスクレイピングインポーター")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="開催日 (YYYYMMDD) — 当日の全レースを一括処理")
    group.add_argument(
        "--race-id", type=int, nargs="+", metavar="ID", help="races.id を指定（複数可）"
    )
    args = parser.parse_args()

    from sqlalchemy import create_engine

    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        if args.date:
            count = import_for_date(session, args.date)
        else:
            count = import_previous_race_extras(session, args.race_id)

        print(f"\n✅ 完了: {count} ペアをDB格納")
