#!/usr/bin/env python3
"""パドックの発走前ウォッチャー（CLI）。

sekito の `bin/scrape/netkeiba-paddock`（scripts_schedules id=64・
`*/3 9-17 * * 6,0,1`）の移設先。中身は `src/scrapers/netkeiba/paddock_job.py`。

3 分ごとに起動し、**発走 20 分以内 かつ 未取得** の中央レースだけを取る。
対象が無ければ 1 リクエストも出さずに終わるので、空振りは安い。

🔴 **この移設で 2 つの不具合が直る**（どちらも 2026-09-06 に実測で確認）:

  1. 文字コード — 移設元は paddock.html を EUC-JP 決め打ちで読んでいたが実際は
     UTF-8。馬名・寸評・評価「穴」が化け、しかも正しい馬名を上書きしていた。
  2. 発走時刻 — 移設元は `sekito.races.start_time` を見ており、日次同期の
     `ON CONFLICT DO NOTHING` で 00:00 に固定されていたため「発走20分以内」に
     永久に一致しなかった。kiseki は `keiba.races.post_time` を直接読む。

⚠️ **まだ cron に載せない。** sekito 側 id=64 が生きているあいだは手動検証だけ。

使い方:
    # いま取りに行くべきレースを見る（取得はしない）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/scrape_netkeiba_paddock.py --list

    # 実行（対象が無ければ即終了）
    ... scripts/scrape_netkeiba_paddock.py
    ... scripts/scrape_netkeiba_paddock.py --dry-run

    # 特定レースを取り直す（埋め戻し用。発走時刻の窓を無視する）
    ... scripts/scrape_netkeiba_paddock.py --date 2026-09-06 --course JHSN --race 1 --force

終了コード: 0（対象 0 件でも 0）。IP 制限で中断したら 2。取得を試みて全滅したら 1。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text  # noqa: E402

from src.config import settings  # noqa: E402
from src.db.session import SyncSessionLocal  # noqa: E402
from src.utils.cron_run import record  # noqa: E402
from src.scrapers.netkeiba import paddock_job  # noqa: E402
from src.scrapers.targets import TargetRace  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")

# 埋め戻し用に、発走時刻の窓を無視して特定レースを引く。
_EXPLICIT_SQL = text(
    """
    SELECT m.code, r.race_number, r.jravan_race_id
    FROM keiba.races r
    JOIN keiba.racecourse_map m ON m.jra_code = r.course
    WHERE r.date = :ymd
      AND (:course IS NULL OR m.code = ANY(:courses))
      AND (:race IS NULL OR r.race_number = ANY(:races))
    ORDER BY m.code, r.race_number
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(description="パドック評価を取得する")
    parser.add_argument("-d", "--date", help="取得日 (YYYY-MM-DD)。--force と併用")
    parser.add_argument("-c", "--course", help="場コード（sekito 4文字・カンマ区切り）")
    parser.add_argument("-n", "--race", help="レース番号（カンマ区切り）")
    parser.add_argument("--force", action="store_true",
                        help="発走時刻の窓と取得済み判定を無視して取り直す（埋め戻し用）")
    parser.add_argument("--list", action="store_true", help="対象レースを表示するだけ")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書かない")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    if not settings.netkeiba_user_id or not settings.netkeiba_password:
        logging.error("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が設定されていません")
        return 2

    with record("scrape_netkeiba_paddock") as run, SyncSessionLocal() as session:
        if args.force:
            target_date = (
                datetime.strptime(args.date, "%Y-%m-%d").date()
                if args.date else datetime.now(JST).date()
            )
            courses = [c.strip() for c in args.course.split(",")] if args.course else None
            races_no = [int(r) for r in args.race.split(",") if r.strip()] if args.race else None
            rows = session.execute(_EXPLICIT_SQL, {
                "ymd": target_date.strftime("%Y%m%d"),
                "course": args.course, "courses": courses or [],
                "race": args.race, "races": races_no or [],
            }).all()
            races = [(TargetRace(target_date, c, int(n)), j or "") for c, n, j in rows]
            logging.info("埋め戻し対象: %d 件", len(races))
        else:
            races = paddock_job.pending_races(session)

        if args.list:
            for target, _ in races:
                print(f"  {target.course_code} {target.race_no}R")
            return 0

        if not races:
            run.summary = "対象0件"
            return 0

        result = paddock_job.scrape(
            session, races,
            user_id=settings.netkeiba_user_id,
            password=settings.netkeiba_password,
            environment_id=settings.scraper_environment_id,
            dry_run=args.dry_run,
        )
        run.summary = (f"対象{result.targets} 成功{result.success}"
                       f" 未公開{result.unavailable} エラー{result.errors}")

    logging.info("パドック取得 終了 (対象 %d / 成功 %d / 未公開 %d / エラー %d)",
                 result.targets, result.success, result.unavailable, result.errors)
    if result.failed_races:
        logging.warning("失敗したレース: %s", ", ".join(result.failed_races[:10]))

    if result.aborted_reason:
        return 2
    # 未公開は正常（発走前に何度も見にいく設計）。全部エラーだったときだけ 1。
    return 1 if result.errors and not (result.success or result.unavailable) else 0


if __name__ == "__main__":
    sys.exit(main())
