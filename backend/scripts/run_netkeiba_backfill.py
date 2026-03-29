#!/usr/bin/env python3
"""netkeiba過去データ遡り収集スクリプト（バックフィル）

月単位で指定し、その月の全開催日のレース備考（remarks）を
netkeiba_race_extras テーブルに直接収集する。

通常の run_netkeiba_import.py（前走データ取得）とは異なり、
レース本体のデータを収集するため、巻き返し指数算出のための
過去データ蓄積に使用する。

使い方:
    # 1ヶ月分を収集
    python scripts/run_netkeiba_backfill.py --year-month 202401

    # 複数月を指定（スペース区切り）
    python scripts/run_netkeiba_backfill.py --year-month 202401 202402 202403

    # ドライラン（スクレイピングせず対象レース数のみ確認）
    python scripts/run_netkeiba_backfill.py --year-month 202401 --dry-run

推定所要時間:
    1ヶ月あたり約 30〜45 分（3〜5 秒/レース × 約 500 レース/月）

注意:
    - NETKEIBA_USER_ID / NETKEIBA_PASSWORD が .env に設定されていること
    - レート制限（429/403）で停止した場合は同じ月を再実行すれば再開可能
      （取得済みレースは自動スキップ）
"""

import argparse
import calendar
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from src.config import settings
from src.importers.netkeiba_importer import import_race_remarks_direct, import_race_remarks_for_month

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_race_dates_in_month(session: Session, year_month: str) -> list[str]:
    """指定月にレースが存在する日付の一覧を返す（YYYYMMDD形式）。"""
    from src.db.models import Race

    year = int(year_month[:4])
    month = int(year_month[4:6])
    start = f"{year_month}01"
    _, last_day = calendar.monthrange(year, month)
    end = f"{year_month}{last_day:02d}"

    dates: list[str] = session.execute(
        select(func.distinct(Race.date))
        .where(Race.date >= start)
        .where(Race.date <= end)
        .where(Race.jravan_race_id.is_not(None))
        .order_by(Race.date)
    ).scalars().all()

    return dates


def show_progress(session: Session, year_month: str, dates: list[str]) -> None:
    """月の収集進捗を表示する。"""
    from src.db.models import NetkeibaRaceExtra, Race, RaceEntry

    year = int(year_month[:4])
    month = int(year_month[4:6])
    start = f"{year_month}01"
    _, last_day = calendar.monthrange(year, month)
    end = f"{year_month}{last_day:02d}"

    # 総出走馬数
    total_horses: int = session.execute(
        select(func.count(RaceEntry.horse_id))
        .join(Race, Race.id == RaceEntry.race_id)
        .where(Race.date >= start)
        .where(Race.date <= end)
        .where(Race.jravan_race_id.is_not(None))
    ).scalar() or 0

    # 取得済みペア数
    collected: int = session.execute(
        select(func.count(NetkeibaRaceExtra.id))
        .join(Race, Race.id == NetkeibaRaceExtra.race_id)
        .where(Race.date >= start)
        .where(Race.date <= end)
    ).scalar() or 0

    pct = (collected / total_horses * 100) if total_horses > 0 else 0
    print(f"  進捗: {collected:,} / {total_horses:,} ペア取得済み ({pct:.1f}%)")
    print(f"  対象開催日数: {len(dates)} 日")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="netkeiba過去レース備考バックフィルスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--year-month",
        nargs="+",
        required=True,
        metavar="YYYYMM",
        help="処理する年月（例: 202401）複数指定可",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="スクレイピングせず対象レース数の確認のみ行う",
    )
    args = parser.parse_args()

    # 入力検証
    for ym in args.year_month:
        if len(ym) != 6 or not ym.isdigit():
            print(f"ERROR: --year-month は YYYYMM 形式で指定してください: {ym!r}", file=sys.stderr)
            sys.exit(1)

    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        for year_month in args.year_month:
            print(f"\n{'='*60}")
            print(f"  {year_month[:4]}年{year_month[4:6]}月 バックフィル開始")
            print(f"{'='*60}")

            dates = get_race_dates_in_month(session, year_month)
            if not dates:
                print(f"  {year_month}: レースデータが見つかりません（DB未取込の可能性）")
                continue

            show_progress(session, year_month, dates)

            if args.dry_run:
                print("  [DRY RUN] スクレイピングはスキップします")
                for d in dates:
                    print(f"    {d}")
                continue

            try:
                month_total = import_race_remarks_for_month(session, year_month)
            except Exception as e:
                logger.error("year_month=%s でエラー発生: %s", year_month, e)
                print(f"\n    ❌ エラー: {e}")
                print("    ※ 再実行すると取得済みはスキップして再開します")
                sys.exit(1)

            print(f"\n  ✅ {year_month[:4]}年{year_month[4:6]}月 完了: 合計 {month_total:,} ペア格納")
            show_progress(session, year_month, dates)
