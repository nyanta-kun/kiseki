#!/usr/bin/env python3
"""自前の入稿記録を netkeirin の**実態**へ合わせる（2026-08-16 新設）。

    python scripts/sync_published_status.py --date 2026-08-16          # 確認だけ
    python scripts/sync_published_status.py --date 2026-08-16 --apply  # 反映

## なぜ要るのか

`netkeirin_submissions.status` は「入稿した（submitted）」「公開した（published）」を
持つが、**netkeirin の画面から人が直接公開すると submitted のまま取り残される**。
実測 2026-08-16: こちらは 35件 submitted なのに netkeirin 側の公開待ちは **0件**
だった（＝全部すでに公開済み）。

netkeirin 側の公開待ち一覧（`action=get_wait`）が唯一の実態なので、
**そこに載っていない submitted は公開済み**として記録を進める。

🔴 **こちらの記録を実態より進めない。** 反映するのは
   「netkeirin の公開待ちに**無い**」と確認できたものだけ。
🔴 **必ず日付で絞る。** 範囲を切らないと過去分まで一括で published になる。
⚠️ 逆向き（published → submitted）はしない。公開は不可逆なので、
   公開済みが公開待ちへ戻ることはありえない。

## 公開待ちが残っているときの扱い

`get_wait` の `list` からレースを特定できなければ**何もしない**（安全側）。
実測できているのは `count == 0` のケースだけで、`list` の形は未確認のため、
特定できない状態で「載っていない＝公開済み」と決めつけない。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.netkeirin_submit_wt import (  # noqa: E402
    STATUS_PUBLISHED,
    STATUS_SUBMITTED,
)
from src.database import get_connection  # noqa: E402
from src.netkeirin_client import NetkeirinClient  # noqa: E402

JST = timezone(timedelta(hours=9))


def _pending_race_ids(items: list) -> set[str] | None:
    """`get_wait` の list から公開待ちの race_id を拾う。拾えなければ None。

    ⚠️ **None と空集合を区別する。** None は「分からない」で、
       空集合は「公開待ちは無い」。取り違えると全件を published にしてしまう。
    """
    if not items:
        return set()
    out: set[str] = set()
    for x in items:
        if isinstance(x, dict):
            for k in ("race_id", "raceId", "race_key"):
                if x.get(k):
                    out.add(str(x[k]))
                    break
            else:
                return None
        elif isinstance(x, (str, int)) and re.fullmatch(r"\d{8,}", str(x)):
            out.add(str(x))
        else:
            return None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD（この日の入稿だけを見る）")
    ap.add_argument("--apply", action="store_true", help="実際に更新する（既定は確認のみ）")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print(json.dumps({"ok": False, "message": f"不正な日付: {args.date}"},
                         ensure_ascii=False))
        return 2

    count, items = NetkeirinClient(propose_only=False).count_wait()
    pending = _pending_race_ids(items)
    if count > 0 and pending is None:
        print(json.dumps({
            "ok": False, "n_wait": count,
            "message": ("netkeirin の公開待ちが残っていますが一覧からレースを特定できません。"
                        "安全のため何もしません（list の形を確認してください）"),
        }, ensure_ascii=False))
        return 1
    pending = pending or set()

    ymd = args.date.replace("-", "")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, rank_key, netkeirin_race_id FROM netkeirin_submissions "
            "WHERE race_key LIKE ? AND status = ?",
            (f"{ymd}%", STATUS_SUBMITTED),
        ).fetchall()
        targets = [(r["race_key"], r["rank_key"]) for r in rows
                   if str(r["netkeirin_race_id"] or "") not in pending]
        if args.apply and targets:
            now = datetime.now(JST).replace(tzinfo=None)
            for race_key, rank_key in targets:
                conn.execute(
                    "UPDATE netkeirin_submissions SET status = ?, published_at = ? "
                    "WHERE race_key = ? AND rank_key = ?",
                    (STATUS_PUBLISHED, now, race_key, rank_key),
                )
            conn.commit()

    print(json.dumps({
        "ok": True, "date": args.date, "applied": bool(args.apply),
        "n_submitted": len(rows), "n_wait_on_netkeirin": count,
        "n_to_publish": len(targets),
        "targets": [f"{rk}#{nk}" for rk, nk in targets],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
