#!/usr/bin/env python3
"""netkeiba の走行データ（個別ラップ・走行距離・位置）を取得する（CLI）。

中身は `src/scrapers/netkeiba/running_data_job.py`。
設計と運用計画: `docs/netkeiba_running_data_plan_2026_09_17.md`。

⚠️ **cron には載せない。** マスターコース契約が要る取得で、契約が無い間は
1着馬しか返らない（その場合、解析結果は既定で書かない）。

使い方:

    cd backend
    # まず数件だけ取って目で見る（契約直後に必ずやる）
    .venv/bin/python scripts/scrape_netkeiba_running_data.py \
        --start 2026-09-05 --end 2026-09-07 --limit 5

    # 走行距離がある範囲（2025-01〜）をまとめて取る
    .venv/bin/python scripts/scrape_netkeiba_running_data.py \
        --start 2025-01-01 --end 2026-06-30

    # 取得はするが書かない / 契約が無い状態の1着馬ぶんも書く
    ... --dry-run
    ... --allow-gated

終了コード: 取れたか既に取ってあれば 0。IP 制限・認証情報なしで中断したら 2。
    対象が有ったのに1件も片付かなければ 1。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.config import settings  # noqa: E402
from src.db.session import SyncSessionLocal  # noqa: E402
from src.scrapers.netkeiba import running_data_job  # noqa: E402

DEFAULT_OUT_DIR = _root / "data" / "netkeiba_running"


def main() -> int:
    p = argparse.ArgumentParser(description="netkeiba の走行データを取得する")
    p.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    p.add_argument("--end", required=True, help="終了日 (YYYY-MM-DD)")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="保存先")
    p.add_argument("--limit", type=int, help="取得する最大レース数（お試し用）")
    p.add_argument("--force", action="store_true", help="生ページがあっても取り直す")
    p.add_argument("--allow-gated", action="store_true",
                   help="🔴 契約が無い（1着馬のみ）ページの解析結果も書く")
    p.add_argument("--dry-run", action="store_true", help="ファイルを書かない")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)

    if not settings.netkeiba_user_id or not settings.netkeiba_password:
        logging.error("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が設定されていません")
        return 2

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    out_dir = Path(args.out_dir)
    logging.info("=== 走行データ取得 開始 (%s〜%s) ===", start, end)

    with SyncSessionLocal() as session:
        result = running_data_job.scrape(
            session, start=start, end=end, out_dir=out_dir,
            user_id=settings.netkeiba_user_id, password=settings.netkeiba_password,
            environment_id=settings.scraper_environment_id,
            force=args.force, allow_gated=args.allow_gated,
            dry_run=args.dry_run, limit=args.limit,
        )

    logging.info(
        "=== 終了 (取得%d / 解析%d頭 / 契約なし%d / 公開待ち%d / 取得済み%d / エラー%d) ===",
        result.fetched, result.parsed_horses, result.gated,
        result.not_published, result.skipped, result.errors)
    if result.failed_races:
        logging.warning("失敗したレース: %s", ", ".join(result.failed_races[:10]))
    if result.aborted_reason:
        logging.error("中断: %s", result.aborted_reason)
        return 2
    return 0 if result.handled else 1


if __name__ == "__main__":
    sys.exit(main())
