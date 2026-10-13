#!/usr/bin/env python3
"""netkeirin の全予想家の商品成績を取り込む（2026-10-13 新設）。

    python scripts/collect_netkeirin_rivals.py                       # 直近3日（日次）
    python scripts/collect_netkeirin_rivals.py --from 2025-04-01     # 過去分（新しい日から）

取り込みの正本は `src/netkeirin_rivals.py`。確定しきったレースは飛ばすので
何度流してもよい（途中で止めても直近から埋まっている）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import netkeirin_rivals  # noqa: E402


def _days(a: date, b: date) -> list[str]:
    out = []
    d = b
    while d >= a:                       # 新しい日から
        out.append(d.isoformat())
        d -= timedelta(days=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD（既定: 今日の --days 日前）")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD（既定: 今日）")
    ap.add_argument("--days", type=int, default=3, help="--from 省略時に遡る日数")
    ap.add_argument("--interval", type=float, default=0.8, help="取得間隔（秒）")
    ap.add_argument("--refetch", action="store_true", help="確定済みのレースも取り直す")
    a = ap.parse_args()

    to = date.fromisoformat(a.date_to) if a.date_to else date.today()
    fr = date.fromisoformat(a.date_from) if a.date_from else to - timedelta(days=a.days - 1)
    total: dict[str, int] = {}
    for d in _days(fr, to):
        races = netkeirin_rivals.races_on([d])
        if not races:
            continue
        st = netkeirin_rivals.collect(races, interval=a.interval, refetch_settled=a.refetch)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
        print(f"{d} {st}", flush=True)
    print(f"完了 {total}", flush=True)
    # 取得失敗・人数不一致は終了コードで知らせる（cron のログだけでは気づけない）
    if total.get("failed") or total.get("mismatch"):
        sys.exit(1)


if __name__ == "__main__":
    main()
