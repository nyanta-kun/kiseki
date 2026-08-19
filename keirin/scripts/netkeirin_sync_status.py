#!/usr/bin/env python3
"""netkeirin 側で公開された分を、こちらの記録へ反映する（2026-08-19 新設）。

    python scripts/netkeirin_sync_status.py 2026-08-19            # 反映する
    python scripts/netkeirin_sync_status.py 2026-08-19 --dry-run  # 差分を出すだけ
    → {"ok": true, "date": "...", "n_submitted": 20, "n_wait": 0,
       "n_synced": 20, "updated": [...], "dry_run": false}

## なぜ要るか

netkeirin では「入稿（下書き）」と「公開」が別操作で、**公開は netkeirin の画面
からも押せる**。そこで押されるとこちらの `netkeirin_submissions.status` は
`submitted`（公開待ち）のまま取り残される。実際 2026-08-16 に 35件、
2026-08-19 に 20件を観測した。

放置すると:
  - 確認画面が「公開待ち N件」と出し続け、押しても netkeirin に対象が無い
  - `/sold-performance` など「売った商品」の母集団が status で割れる

## 判定

netkeirin の**公開待ち一覧**（`action=get_wait`）に載っていない `submitted` は
「もうこちらの手を離れた」とみなして `published` にする。

🔴 **取得に失敗したときは1件も触らない。** `wait_state()` が ok=False を返したら
   即座に降りる。`count_wait()` は失敗時も `(0, [])` を返すので、それを根拠に
   書くと**通信が落ちた日にその日の入稿を全部「公開済み」にしてしまう**。

⚠️ **「公開された」と「netkeirin 側で削除された」は区別できない。**
   netkeirin に公開済み一覧の API が無いため、公開待ちから消えた理由までは
   分からない。既定では公開扱いにする（画面から取り消せば `deleted` になるので、
   画面外で消えるのは実運用では稀）。UI 側の確認文言でもそう説明すること。

⚠️ **逆向き（published → submitted）はしない。** 公開は不可逆なので、
   戻す変更は必ず誤りになる。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.netkeirin_client import NetkeirinClient  # noqa: E402

STATUS_SUBMITTED = "submitted"
STATUS_PUBLISHED = "published"


def _wait_race_ids(items: list) -> set[str]:
    """`get_wait` の一覧から netkeirin の race_id を取り出す。

    ⚠️ 応答の要素は dict とは限らない（実測は空配列しか観測できていない）。
       文字列でも dict でも拾えるようにし、**解釈できない形なら空集合ではなく
       例外**にする——空集合だと「全部公開された」と読めてしまうため。
    """
    out: set[str] = set()
    for it in items:
        if isinstance(it, dict):
            rid = it.get("race_id") or it.get("raceId") or it.get("id")
            if rid is None:
                raise ValueError(f"get_wait の要素に race_id がありません: {it!r}")
            out.add(str(rid))
        elif isinstance(it, (str, int)):
            out.add(str(it))
        else:
            raise ValueError(f"get_wait の要素を解釈できません: {it!r}")
    return out


def sync(date: str, dry_run: bool) -> dict:
    ok, n_wait, items = NetkeirinClient(propose_only=False).wait_state()
    if not ok:
        return {"ok": False, "date": date, "n_synced": 0,
                "message": "netkeirin の公開待ち一覧を取得できませんでした。"
                           "状態が確認できないので記録は変更していません。"}
    try:
        waiting = _wait_race_ids(items)
    except ValueError as e:
        return {"ok": False, "date": date, "n_synced": 0, "message": str(e)}

    # ⚠️ 日付の範囲は Python 側で作る。`?::date + 1` のような方言を SQL に
    #    書くと SQLite 互換レイヤ（tests）で落ちる。
    day = Date.fromisoformat(date)
    lo = f"{day} 00:00:00"
    hi = f"{day + timedelta(days=1)} 00:00:00"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, rank_key, venue_name, race_no, netkeirin_race_id "
            "FROM netkeirin_submissions "
            "WHERE status = ? AND submitted_at >= ? AND submitted_at < ?",
            (STATUS_SUBMITTED, lo, hi),
        ).fetchall()
        targets = [r for r in rows
                   if str(r[4] or "") not in waiting]
        updated = [{"race_key": r[0], "rank_key": r[1],
                    "venue_name": r[2], "race_no": r[3]} for r in targets]
        if targets and not dry_run:
            conn.executemany(
                "UPDATE netkeirin_submissions "
                "SET status = ?, published_at = COALESCE(published_at, ?) "
                "WHERE race_key = ? AND rank_key = ? AND status = ?",
                [(STATUS_PUBLISHED, now, r[0], r[1], STATUS_SUBMITTED) for r in targets],
            )
            conn.commit()
    return {"ok": True, "date": date, "n_submitted": len(rows), "n_wait": n_wait,
            "n_synced": len(targets), "updated": updated, "dry_run": dry_run,
            "message": (f"公開待ち {len(rows)}件 のうち {len(targets)}件 を"
                        f"{'（確認のみ）' if dry_run else ''}公開済みにしました"
                        f"（netkeirin の公開待ちは {n_wait}件）")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="YYYY-MM-DD（この日の入稿だけを対象にする）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = sync(args.date, args.dry_run)
    print(json.dumps(res, ensure_ascii=False, default=str))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
