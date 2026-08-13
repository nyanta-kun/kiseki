#!/usr/bin/env python3
"""`wt_races.cup_grade` / `cup_name` の過去分バックフィル（2026-08-14 新設）。

開催グレード（GP/GI/GII/GIII/FI/FII）は 2026-08-14 に保存を始めたため、
それ以前のレースは NULL。過去の大会を判別できるようにするため埋める。

## レース単位ではなく**開催（cup_id）単位**で引く

`cup.grade` は開催の属性なので、1開催あたり1リクエストで足りる。
2026年で約280開催なので、レース単位（約17,000）の 1/60 で済む。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/backfill_cup_grade_wt.py \\
        --start 2026-01-01 --end 2026-08-13 [--dry-run] [--sleep 1.2]

⚠️ 外部サイトへの取得を伴う。`--sleep` を極端に小さくしないこと（既定1.2秒）。
⚠️ 既に埋まっている開催は既定でスキップする（`--force` で上書き）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.cup_grade import grade_label, is_known_grade  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.scraper.winticket import (  # noqa: E402
    _BASE, VENUE_SLUGS, WinticketScraper, _extract_state, _get_query,
)


def _cups(date_from: str, date_to: str, force: bool) -> list[tuple[str, str, int]]:
    """(cup_id, venue_id, day_index) を開催ごとに1件返す。"""
    cond = "" if force else " AND cup_grade IS NULL"
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT cup_id, venue_id, MIN(day_index) AS d FROM wt_races "
            f"WHERE race_date BETWEEN ? AND ? AND cup_id IS NOT NULL{cond} "
            f"GROUP BY cup_id, venue_id ORDER BY cup_id",
            (date_from, date_to),
        ).fetchall()
    return [(r["cup_id"], r["venue_id"], int(r["d"] or 1)) for r in rows]


def _fetch_cup(sc: WinticketScraper, cup_id: str, venue_id: str, day: int):
    """(grade, name) を返す。取れなければ (None, None)。"""
    slug = VENUE_SLUGS.get(venue_id)
    if not slug:
        return None, None
    resp = sc._get(f"{_BASE}/keirin/{slug}/racecard/{cup_id}/{day}/1")
    if resp is None or resp.status_code != 200:
        return None, None
    state = _extract_state(resp.text)
    for q in ("FETCH_KEIRIN_CUP_RACES", "FETCH_KEIRIN_RACE"):
        src = _get_query(state, q) or {}
        cup = src.get("cup")
        if not cup:
            cup = next((c for c in (src.get("cups") or [])
                        if str(c.get("id")) == str(cup_id)), None)
        if cup:
            return cup.get("grade"), cup.get("name")
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=1.2)
    ap.add_argument("--force", action="store_true", help="埋まっている開催も上書きする")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cups = _cups(args.start, args.end, args.force)
    print(f"[cup-grade] 対象 {len(cups)} 開催（{args.start}〜{args.end}）", flush=True)
    sc = WinticketScraper()
    n_ok = n_ng = n_unknown = 0
    for i, (cup_id, venue_id, day) in enumerate(cups, 1):
        grade, name = _fetch_cup(sc, cup_id, venue_id, day)
        if grade is None:
            n_ng += 1
            print(f"  [{i}/{len(cups)}] {cup_id}: 取得できず", flush=True)
        else:
            # 🔴 未知のコードは**黙って通さない**。対応表を見直す合図として残す。
            if not is_known_grade(grade):
                n_unknown += 1
                print(f"  [{i}/{len(cups)}] {cup_id}: ⚠️ 未知の grade={grade!r} "
                      f"({name}) — keirin_cup_grade.py の対応表を確認すること", flush=True)
            n_ok += 1
            if not args.dry_run:
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE wt_races SET cup_grade=?, cup_name=? WHERE cup_id=?",
                        (int(grade), name, cup_id))
                    conn.commit()
            if i % 20 == 0 or is_known_grade(grade) is False:
                print(f"  [{i}/{len(cups)}] {cup_id} grade={grade}"
                      f"({grade_label(grade)}) {name}", flush=True)
        time.sleep(args.sleep)
    print(f"[cup-grade] 完了: 成功{n_ok} / 失敗{n_ng} / 未知コード{n_unknown}"
          + ("（dry-run のため未書込）" if args.dry_run else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
