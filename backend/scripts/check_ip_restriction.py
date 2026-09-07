#!/usr/bin/env python3
"""netkeiba の IP 制限の解除チェックと自動復旧（CLI）。

sekito の `bin/maintenance/check-ip-restriction`（scripts_schedules id=34・
毎時）の移設先。中身は `src/scrapers/netkeiba/ip_restriction.py`。

## 何をするか

制限フラグ（`sekito.system_settings.netkeiba_ip_restricted*`）が立っていたら、
netkeiba へ 1 回だけアクセスして通れるか確かめる。通れればフラグを落とす。
立っていなければ 1 リクエストも出さずに終わる。

## 🔴 `scheduler_enabled` の後始末も兼ねる

移植版の検出処理は `scheduler_enabled` を false にしないが、過去に sekito 側が
落とした値が残っていることがある。`scheduler.js` の `runJob()` はこの値を見ない
ので普段は無害だが、**その状態で sekito のコンテナが再起動すると
`loadSchedules()` が何も読まず、スケジューラごと止まって自力で戻れなくなる。**
毎時走るこのジョブで、見つけたら直す。

使い方:
    # 状態を見るだけ
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/check_ip_restriction.py --status

    # 解除チェックと復旧（cron はこちら）
    ... scripts/check_ip_restriction.py

終了コード: 常に 0（制限中でも異常ではない）。復旧に失敗しても 0。
    ⚠️ 非 0 を返すと、制限中のあいだ毎時アラートが鳴り続けて
    「いつも赤い監視」になる。制限の事実は Discord 通知と
    `check_scrape_supply` の網羅率で見る。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402
from src.scrapers.netkeiba import ip_restriction  # noqa: E402
from src.utils.cron_run import record  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="netkeiba の IP 制限を確認・復旧する")
    parser.add_argument("--status", action="store_true",
                        help="状態を表示するだけ（ネットワークへ出ない）")
    parser.add_argument("--check", action="store_true",
                        help="制限の有無にかかわらずアクセス可否を試す")
    parser.add_argument("--no-notify", action="store_true", help="Discord へ送らない")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    with record("check_ip_restriction") as run, SyncSessionLocal() as session:
        keys = ip_restriction.restricted_keys(session)

        if args.status:
            print("IP 制限:", "あり" if keys else "なし")
            for k in keys:
                print("  ", k)
            run.summary = f"状態確認: {'制限中' if keys else '正常'}"
            return 0

        if args.check:
            ok = ip_restriction.can_access()
            print("netkeiba へのアクセス:", "可" if ok else "不可")
            run.summary = f"アクセス確認: {'可' if ok else '不可'}"
            return 0

        recovered = ip_restriction.try_recover(session, notify=not args.no_notify)
        if not keys:
            run.summary = "制限なし"
        elif recovered:
            run.summary = f"復旧: {', '.join(keys)} を解除"
        else:
            run.summary = f"制限中（{', '.join(keys)}）— まだ解除されていません"

    return 0


if __name__ == "__main__":
    sys.exit(main())
