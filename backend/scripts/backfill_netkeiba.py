#!/usr/bin/env python3
"""化けた列を netkeiba から取り直して埋め戻す（CLI）。

## 背景

2026-09-06 に見つけた文字化けのうち、**netkeiba にしか無い情報**を取り直す。
馬名と中央の血統は JV-Link / UmaConn から書き戻せたが（`repair_netkeiba_horse_names.py`）、
以下は元データが netkeiba にしか無い:

    blood     1,550 行 /  402 レース（全て地方）  中央登録歴の無い地方専用馬の父・母父
    training  4,754 行 /  366 レース（全て中央）  "キビキビ A" 等の独自の短評
    paddock     391 行 /   73 レース（全て中央）  パドックの寸評

計 841 リクエスト。夜間（レート制限が最も緩い時間帯）で 1.5〜2 時間の見込み。
（2026-09-07 に完了済み。残 0 件）

## `--target time_index` は別枠

こちらは化けではなく **そもそも行が無い** ものを埋める。`netkeiba-index` が
2026-05 以降ずっと 10 分でタイムアウト kill されていたため約 3,600 レースが欠けている
（2026-09-07 実測 3,607・115 日ぶん）。sekito 側の `backfill-netkeiba-time-index`
(id=96) がこれを担っていたが、**そのジョブ自体も 10 分 kill されていて実質進んで
いなかった**（初回で 32 レースのみ）。kiseki の cron には scheduler のタイムアウトが
無いので、`--minutes` の予算がそのまま効く。

## 使い方

    # 対象件数を数えるだけ
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/backfill_netkeiba.py --target training --count

    # 実行（予算 110 分。既存の backfill ジョブと同じ考え方）
    ... scripts/backfill_netkeiba.py --target training --minutes 110

    # 少しだけ試す
    ... scripts/backfill_netkeiba.py --target paddock --limit 3 --dry-run

    # 3 つ全部を順に（夜間バッチ想定）
    ... scripts/backfill_netkeiba.py --target all --minutes 110

    # タイム指数の欠損を埋める（化けの修復とは別枠）
    ... scripts/backfill_netkeiba.py --target time_index --count
    ... scripts/backfill_netkeiba.py --target time_index --minutes 110

⚠️ **状態を持たない。** 対象は「いま化けている行」から毎回引き直すので、
   途中で止まっても次回そのまま続きから走る。予算時間で切ってよい。

終了コード: 0（対象 0 件でも 0）。IP 制限で中断したら 2。
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text  # noqa: E402

from src.config import settings  # noqa: E402
from src.db.session import SyncSessionLocal  # noqa: E402
from src.utils.cron_run import RunRecord, record  # noqa: E402
from src.scrapers.netkeiba import backfill  # noqa: E402
from src.scrapers.netkeiba.store import REPLACEMENT_CHAR  # noqa: E402

# 化けの修復（対象が少ない順）。`time_index` は別枠（欠損の埋め戻し）。
ORDER = ("paddock", "training", "blood")
TIME_INDEX = "time_index"


def _count(session, target: str) -> tuple[int, int]:
    """(化けている行数, レース数) を返す。"""
    row = session.execute(
        text(
            f"""
            SELECT count(*) AS rows,
                   count(DISTINCT (date, course_code, race_no)) AS races
            FROM sekito.netkeiba
            WHERE {backfill.TARGETS[target]}
            """
        ),
        {"bad": REPLACEMENT_CHAR},
    ).fetchone()
    return int(row[0]), int(row[1])


def main() -> int:
    parser = argparse.ArgumentParser(description="化けた netkeiba データを取り直す")
    parser.add_argument("--target", choices=(*ORDER, TIME_INDEX, "all"), default="all",
                        help="取り直す項目。all は寸評→調教→血統の順（対象が少ない順）。"
                             "time_index は別枠で、化けではなく**欠損**を埋める")
    parser.add_argument("--minutes", type=float,
                        help="予算時間（分）。超えたら途中で止める")
    parser.add_argument("--limit", type=int, help="対象レース数の上限（試験用）")
    parser.add_argument("--count", action="store_true", help="件数を数えるだけ")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書かない")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    targets = list(ORDER) if args.target == "all" else [args.target]
    is_time_index = args.target == TIME_INDEX

    # 🔴 `--count` と `--dry-run` は記録しない。
    #    数えるだけ・試すだけの実行を cron_runs に残すと、監視から見て
    #    「ジョブが正常に走った」と区別がつかなくなる。手で叩いた回数で
    #    「今日は走った」ことになってしまい、本物の未実行を見逃す。
    ctx = (
        nullcontext(RunRecord(job_name="(記録しない)"))
        if (args.count or args.dry_run)
        else record(f"backfill_netkeiba:{args.target}")
    )
    with ctx as run, SyncSessionLocal() as session:
        if is_time_index and args.count:
            pending = backfill.missing_time_index_races(session)
            days = len({t.date for t, _ in pending})
            logging.info("time_index 欠損 %d レース / %d 日ぶん", len(pending), days)
            return 0

        if args.count:
            for t in targets:
                rows, races = _count(session, t)
                logging.info("%-9s 化け %5d 行 / %4d レース", t, rows, races)
            return 0

        if not settings.netkeiba_user_id or not settings.netkeiba_password:
            logging.error("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が設定されていません")
            return 2

        if is_time_index:
            pending = backfill.missing_time_index_races(session, limit=args.limit)
            days = len({t.date for t, _ in pending})
            logging.info("=== time_index: 欠損 %d レース / %d 日ぶん ===",
                         len(pending), days)
            result = backfill.run(
                session, TIME_INDEX, pending,
                user_id=settings.netkeiba_user_id,
                password=settings.netkeiba_password,
                environment_id=settings.scraper_environment_id,
                budget_seconds=args.minutes * 60 if args.minutes else None,
                dry_run=args.dry_run,
            )
            logging.info("成功 %d / 空 %d / エラー %d", result.success,
                         result.empty, result.errors)
            if result.failed_races:
                logging.warning("失敗: %s", ", ".join(result.failed_races[:5]))
            remaining = backfill.missing_time_index_races(session)
            logging.info("=== 残り %d レース ===", len(remaining))
            run.summary = (f"対象{len(pending)} 成功{result.success} 空{result.empty}"
                           f" エラー{result.errors} 残り{len(remaining)}")
            return 2 if result.aborted_reason and result.aborted_reason != "budget" else 0

        # 予算は対象間で分け合う。1 つ目が食い尽くさないようにする。
        budget = (args.minutes * 60 / len(targets)) if args.minutes else None
        aborted = False

        for t in targets:
            rows, races = _count(session, t)
            logging.info("=== %s: 化け %d 行 / %d レース ===", t, rows, races)
            if races == 0:
                continue

            pending = backfill.pending_races(session, t, limit=args.limit)
            result = backfill.run(
                session, t, pending,
                user_id=settings.netkeiba_user_id,
                password=settings.netkeiba_password,
                environment_id=settings.scraper_environment_id,
                budget_seconds=budget,
                dry_run=args.dry_run,
            )
            if result.failed_races:
                logging.warning("失敗: %s", ", ".join(result.failed_races[:5]))
            if result.aborted_reason and result.aborted_reason != "budget":
                logging.error("中断: %s", result.aborted_reason)
                aborted = True
                break

        logging.info("=== 残りの化け ===")
        left = []
        for t in targets:
            rows, races = _count(session, t)
            logging.info("%-9s 化け %5d 行 / %4d レース", t, rows, races)
            left.append(f"{t}={races}R")
        run.summary = "残り " + " ".join(left)

    return 2 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
