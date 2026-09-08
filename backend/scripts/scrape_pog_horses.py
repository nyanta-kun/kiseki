#!/usr/bin/env python3
"""POG 指名候補（2歳馬）の一覧を netkeiba から取得する（CLI）。

sekito の `bin/scrape/netkeiba-horses-bulk`（スケジュール未登録・年1回の手動運用）の
移設先。中身は `src/scrapers/netkeiba/pog_horse_list.py`。

## なぜ要るか

POG はデビュー前の2歳馬を指名するので、JV-Link にまだ載らない馬の一覧が要る。
`keiba.horses` は SE（出走）由来なので出走した馬しか居ない
（2026-09-08 実測: 2024年産は netkeiba 7,943頭に対し `keiba.horses` は 1,235頭）。

## 使い方

    # 当年の2歳世代（既定）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/scrape_pog_horses.py

    # 世代を指定 / 途中から再開 / DB へ書かない
    ... scripts/scrape_pog_horses.py --birth-year 2024
    ... scripts/scrape_pog_horses.py --from-page 40 --to-page 60
    ... scripts/scrape_pog_horses.py --dry-run

## 所要時間

100件/ページで 2024年産は 80 ページ。レートリミッタの待ちが支配的。
**打ち切らないこと**（netkeiba-index と同じ理由）。

終了コード: 0 = 取得できた / 1 = 1 件も取れなかった / 2 = 中断（IP 制限・構造変化など）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.config import settings  # noqa: E402
from src.db.session import SyncSessionLocal  # noqa: E402
from src.scrapers.netkeiba import pog_horse_list  # noqa: E402
from src.scrapers.netkeiba.rate_limiter import RateLimiter  # noqa: E402
from src.scrapers.netkeiba.session import USER_AGENTS, login  # noqa: E402
from src.utils.cron_run import RunRecord, record  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    parser = argparse.ArgumentParser(description="POG 指名候補の2歳馬一覧を取得する")
    parser.add_argument("--birth-year", type=int,
                        help="生産年。既定は当年の2歳世代（今年 - 2）")
    parser.add_argument("--from-page", type=int, default=1, help="開始ページ（再開用）")
    parser.add_argument("--to-page", type=int, help="終了ページ")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書かない")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    today = datetime.now(JST).date()
    birth_year = args.birth_year if args.birth_year else today.year - 2

    logging.info("=== POG 馬一覧取得 開始 (生産年=%d) ===", birth_year)
    # 🔴 `--dry-run` は記録しない。試し打ちを cron_runs に残すと、監視から見て
    #    「ジョブが正常に走った」と区別がつかず、本物の未実行を見逃す。
    ctx = (
        nullcontext(RunRecord(job_name="(記録しない)"))
        if args.dry_run
        else record("scrape_pog_horses")
    )
    with ctx as run, SyncSessionLocal() as session:
        limiter = RateLimiter()
        # 一覧は未ログインでも引けるが、他のジョブと同じ足跡にするためログインする。
        # 認証情報が無ければ素のセッションで続行する（一覧に会員限定要素は無い）。
        if settings.netkeiba_user_id and settings.netkeiba_password:
            http = login(settings.netkeiba_user_id, settings.netkeiba_password,
                         rate_limiter=limiter)
        else:
            logging.warning("netkeiba の認証情報が無いため未ログインで取得します")
            http = requests.Session()
            http.headers.update({"User-Agent": USER_AGENTS[0]})

        result = pog_horse_list.scrape(
            session,
            birth_year=birth_year,
            today=today,
            http=http,
            limiter=limiter,
            from_page=args.from_page,
            to_page=args.to_page,
            dry_run=args.dry_run,
            environment_id=settings.scraper_environment_id,
        )
        run.summary = (
            f"生産年{birth_year} 総件数{result.total_reported} "
            f"取得{result.parsed} 保存{result.saved} "
            f"ページ{result.pages_fetched} 世代違い{result.birth_year_mismatch} "
            f"エラー{result.errors}"
            + (f" 中断:{result.aborted_reason}" if result.aborted_reason else "")
        )

    logging.info(
        "=== 取得 終了 (総件数 %d / 取得 %d / 保存 %d / ページ %d / エラー %d) ===",
        result.total_reported, result.parsed, result.saved,
        result.pages_fetched, result.errors,
    )
    if result.aborted_reason:
        logging.error("中断: %s", result.aborted_reason)
        return 2
    if result.parsed == 0:
        logging.error("1 件も取得できませんでした")
        return 1
    # 総件数の 80% に届かないのは「途中で静かに止まった」形。人が気づけるように残す。
    #
    # 🔴 一部だけを取る指定（--from-page / --to-page）では鳴らさない。
    #    2026-09-08 の初回実走で 31 ページ目まで進んだところで接続が切れ、
    #    `--from-page 32` で再開したところ、**正常に完走したのに警告が出た**
    #    （49/80 ページぶんしか取っていないので当然そうなる）。
    #    毎回鳴る警告は読まれなくなり、本物の中断を隠す。
    partial = args.from_page > 1 or args.to_page is not None
    if not partial and result.parsed < result.total_reported * 0.8:
        logging.warning(
            "取得 %d 件は総件数 %d の 80%% 未満です。再実行を検討してください",
            result.parsed, result.total_reported,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
