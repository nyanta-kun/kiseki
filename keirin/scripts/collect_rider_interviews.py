#!/usr/bin/env python3
"""選手コメント（前検日・レース後）を winticket から取って `rider_interviews` へ保存する（2026-10-01）。

    # レース後コメント: その日の全レース（結果が出てから。夜と翌朝に2回回す）
    PYTHONPATH=. .venv/bin/python3 scripts/collect_rider_interviews.py post 2026-10-01
    # 前検日コメント: その日が**初日**の開催の全レース（前日の夜と当日朝に回す）
    PYTHONPATH=. .venv/bin/python3 scripts/collect_rider_interviews.py pre 2026-10-02

- 7車・9車以外も取る（表示は全レースで行うため）
- 同じページを何度読んでも行は増えない（本文が変わった行だけ差し替え、分類を消して付け直させる）
- ページの読み込みは 1秒に1件。失敗は数えて最後に出す（途中で止めない）
- 分類は別のジョブ（`scripts/tag_rider_interviews.py`・Mac mini の `claude -p`）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.rider_interviews import fetch_interview_data, page_url, rows_from_data, upsert  # noqa: E402


def targets(kind: str, date: str) -> list[dict]:
    """取るページの一覧。post はその日の全レース、pre はその日が初日の開催の全レース。"""
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_key, venue_id, race_date, race_no, cup_id, day_index FROM wt_races "
            "WHERE race_date = ? AND cup_id IS NOT NULL ORDER BY race_key", (date,))]
        if kind == "post":
            return rows
        # 初日かどうかは「その開催でこの日より前のレースが無い」で決める（day_index=1 決め打ちは
        # しない。前検日の翌日が day_index=1 とは限らない開催がある）。
        firsts = {r["cup_id"] for r in c.execute(
            "SELECT cup_id FROM wt_races WHERE cup_id IN (SELECT cup_id FROM wt_races WHERE race_date = ?) "
            "GROUP BY cup_id HAVING MIN(race_date) = ?", (date, date))}
    return [r for r in rows if r["cup_id"] in firsts]


def main() -> None:
    """CLI エントリ。"""
    ap = argparse.ArgumentParser(description="選手コメントの取得")
    ap.add_argument("kind", choices=["pre", "post"])
    ap.add_argument("date", help="YYYY-MM-DD（post=レース日 / pre=開催初日）")
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    pages = targets(args.kind, args.date)
    ins = upd = empty = fail = 0
    for r in pages:
        url = page_url(args.kind, r["venue_id"], r["cup_id"], int(r["day_index"]), int(r["race_no"]))
        try:
            data = fetch_interview_data(url, args.kind)
        except Exception as e:  # 1ページの失敗で全体を止めない
            fail += 1
            print(f"[fail] {url} {e!r}"[:200])
            continue
        rows = rows_from_data(args.kind, r["race_key"], r["cup_id"], str(r["race_date"]), data)
        if not rows:
            empty += 1
        with get_connection() as c:
            a, b = upsert(c, rows)
        ins += a
        upd += b
        time.sleep(args.sleep)
    print(f"[rider-interviews] {args.kind} {args.date}: ページ {len(pages)} / 追加 {ins} / 本文更新 {upd} "
          f"/ 本文なし {empty} / 失敗 {fail}")


if __name__ == "__main__":
    main()
