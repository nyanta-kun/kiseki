#!/usr/bin/env python3
"""未承認の入稿案を Discord で催促する（2026-08-11 新設）。

承認制（`netkeirin_settings._global.require_approval`）のとき、承認しないまま
波の締切が来ると **その日の商品が出ない**。締切前に一度だけ催促を出す。

    python scripts/notify_pending_approvals_wt.py            # 当日
    python scripts/notify_pending_approvals_wt.py 2026-08-11

🔴 **催促だけで自動入稿はしない**（ユーザー指示・2026-08-11）。
   未承認のまま締切を過ぎたレースは見送りになる。記録（status='proposed'）は
   残るので、後から「何を出し損ねたか」は追える。

⚠️ 承認制が OFF のときは何もしない。自動入稿されるので催促する相手がいない。

cron 例（波の締切30分前）:
    30 8,12,17 * * * $KEIRIN_HOME/.venv/bin/python3 scripts/notify_pending_approvals_wt.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.netkeirin_submit_wt import (  # noqa: E402
    REVIEW_URL,
    STATUS_PROPOSED,
    _approval_required,
)
from src.database import get_connection  # noqa: E402
from src.notify.discord import send  # noqa: E402

JST = timezone(timedelta(hours=9))


def pending(date: str) -> list[dict]:
    """未承認の入稿案（発走前のものだけ）。

    発走済みは催促しても入稿できないので除く（netkeirin は発走後に売れない）。
    """
    ymd = date.replace("-", "")
    now_ts = int(datetime.now(JST).timestamp())
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT s.venue_name, s.race_no, s.rank_key, r.start_at "
            "FROM netkeirin_submissions s "
            "LEFT JOIN wt_races r ON r.race_key = s.race_key "
            "WHERE s.race_key LIKE ? AND s.status = ? "
            "ORDER BY r.start_at, s.venue_name, s.race_no",
            (f"{ymd}%", STATUS_PROPOSED),
        ).fetchall()
    return [dict(r) for r in rows
            if not r["start_at"] or int(r["start_at"]) > now_ts]


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(JST).strftime("%Y-%m-%d")
    if not _approval_required():
        print("[pending-approvals] 承認制ではないので何もしません", flush=True)
        return 0

    rows = pending(date)
    if not rows:
        print("[pending-approvals] 未承認はありません", flush=True)
        return 0

    by_venue: dict[str, int] = {}
    for r in rows:
        by_venue[r["venue_name"]] = by_venue.get(r["venue_name"], 0) + 1
    breakdown = " / ".join(f"{v} {n}件" for v, n in by_venue.items())
    msg = (f"⏰ **[netkeirin未承認] {date}: {len(rows)}件が未入稿のままです**\n"
           f"{breakdown}\n"
           f"承認: {REVIEW_URL}\n"
           f"⚠️ 承認しないまま発走すると見送りになります（自動入稿はしません）。")
    try:
        send(msg, channel="netkeirin")
    except Exception as e:  # noqa: BLE001 — 通知失敗で cron を落とさない
        print(f"[pending-approvals] Discord通知失敗: {e}", flush=True)
        return 1
    print(f"[pending-approvals] {len(rows)}件を催促しました", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
