"""netkeirin 売上の日次レポート（2026-08-16 新設）。

毎朝 9:40 の売上取り込み（`scripts/scrape_netkeirin_sales.sh`）のあとに、前日の
販売実績と当月の累計売上を Discord へ送るための計算・文面・送信をまとめる。

## 🔴 標準ライブラリ以外を import しないこと

このモジュールは **2つの Python 環境から読まれる**:

  - kiseki backend（FastAPI・`/api/keirin/netkeirin-sales` が売上率を使う）
  - **VPS の keirin venv**（`backend/scripts/scrape_netkeirin_sales.py` が
    そちらの venv で動く。FastAPI も SQLAlchemy も無い）

`requests` すら入れてはいけない（backend の CI venv に無く、入れると
**Web は無事なまま通知だけが落ちる**）。HTTP は `urllib` で足りる。
同じ制約が `services/keirin_marquee.py` にもある（そちらは keirin が
ファイル読み込みで束縛している）。

## 売上の定義

    売上 = 販売*有償*pt × REVENUE_RATE

🔴 `sold_points`（販売pt）には**無償ptが混ざり収益にならない**。
   画面にも通知にも両方を並べて出すこと。片方だけ見せると収益を誤読する。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

#: 売上金額 = 販売有償pt × この率。**ここが唯一の正本**
#: （API も日次通知もこの値を読む。写して使わないこと）。
REVENUE_RATE = 0.30

#: Discord へ送るときのタイムアウト（秒）
_TIMEOUT = 15


def revenue_yen(sold_paid_points: int | float | None) -> int:
    """販売有償pt から売上（円）を出す。

    ⚠️ **販売pt（`sold_points`）を渡さないこと。** 無償ptが混ざっており、
       そのまま掛けると売上を過大に見せる。
    """
    return round(int(sold_paid_points or 0) * REVENUE_RATE)


def build_sales_message(s: Mapping[str, Any]) -> str:
    """日次売上の本文を組む。

    必要なキー: `sale_date`(YYYYMMDD) / `n_sold` / `sold_points` /
    `sold_paid_points` / `month_n_days` / `month_sold_paid_points`
    """
    d = str(s["sale_date"])
    return (
        f"💰 **netkeirin 売上 {d[:4]}-{d[4:6]}-{d[6:]}**\n"
        f"```\n"
        f"販売点数     {int(s['n_sold'] or 0):,} 点\n"
        f"販売pt       {int(s['sold_points'] or 0):,} pt\n"
        f"販売有償pt   {int(s['sold_paid_points'] or 0):,} pt\n"
        f"売上         {revenue_yen(s['sold_paid_points']):,} 円"
        f"  (有償pt × {REVENUE_RATE})\n"
        f"---\n"
        f"{d[4:6]}月 累計   {revenue_yen(s['month_sold_paid_points']):,} 円"
        f"  (有償 {int(s['month_sold_paid_points'] or 0):,} pt"
        f" / {int(s['month_n_days'] or 0)}日)\n"
        f"```"
    )


def post_to_discord(webhook_url: str, content: str) -> bool:
    """Discord へ送る。成功で True。

    ⚠️ **例外を投げない。** 呼び出し元（売上の取り込み）は通知の失敗で
       落ちてはいけない —— データは既に DB に入っており、通知はその報告でしかない。
       ここで落とすと「スクレイプが失敗した」ように見える。
    """
    if not webhook_url:
        return False
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "kiseki-netkeirin-sales/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
