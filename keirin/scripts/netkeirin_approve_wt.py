#!/usr/bin/env python3
"""入稿案の承認・取消を行う CLI（2026-08-11 新設）。

確認画面（`/keirin/review`）から webhook 経由で呼ばれる。**結果を JSON で
標準出力へ出す**ので、呼び出し側は成否をその場で画面へ返せる。

    python scripts/netkeirin_approve_wt.py approve --race-key 20260811_13_01 --rank-key 7C
    python scripts/netkeirin_approve_wt.py approve --date 2026-08-11 --venue 前橋
    python scripts/netkeirin_approve_wt.py cancel  --race-key 20260811_13_01 --rank-key 7C

## なぜ同期実行なのか

既存の webhook（`/submit-race` 等）は `_spawn` で背景起動し「開始しました」だけを
返す。だが確認画面は**承認した結果をその場で見せる**必要がある
（承認したのに出ていない、が最も困る）。1レースあたり netkeirin への POST 1回で
済むので、同期で走らせて結果を返す。

## 🔴 承認は買い目を再計算しない

`approve_and_submit()` が保存済みの `legs`/`marks` をそのまま送る。
ここで候補ファイルを読み直すと、確認画面で見たものと違うものが入稿される。

## 🔴 場単位でも「1件ずつの結果」を返す

まとめて成功/失敗の件数だけ返すと、どのレースが失敗したのか画面で示せない。
途中で失敗しても**残りは続行**する（1件の失敗で場全体が止まらない）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.netkeirin_submit_wt import (  # noqa: E402
    STATUS_PROPOSED,
    approve_and_submit,
    cancel_submission,
)
from src.database import get_connection  # noqa: E402


def _proposals_for_venue(date: str, venue_name: str) -> list[tuple[str, str]]:
    """その日・その場の入稿案を (race_key, rank_key) で返す（発走順）。

    `date` は YYYY-MM-DD。race_key の先頭8桁が YYYYMMDD。
    """
    ymd = date.replace("-", "")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, rank_key FROM netkeirin_submissions "
            "WHERE race_key LIKE ? AND venue_name = ? AND status = ? "
            "ORDER BY race_no",
            (f"{ymd}%", venue_name, STATUS_PROPOSED),
        ).fetchall()
    return [(r["race_key"], r["rank_key"]) for r in rows]


def _run(action: str, targets: list[tuple[str, str]]) -> dict:
    fn = approve_and_submit if action == "approve" else cancel_submission
    results = []
    for race_key, rank_key in targets:
        try:
            ok, message = fn(race_key, rank_key)
        except Exception as e:  # noqa: BLE001 — 1件の失敗で残りを止めない
            ok, message = False, f"例外: {e}"
        results.append({"race_key": race_key, "rank_key": rank_key,
                        "ok": bool(ok), "message": str(message)})
    n_ok = sum(1 for r in results if r["ok"])
    return {"ok": n_ok == len(results) and bool(results),
            "n_ok": n_ok, "n_ng": len(results) - n_ok, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("approve", "cancel"))
    ap.add_argument("--race-key")
    ap.add_argument("--rank-key")
    ap.add_argument("--date")
    ap.add_argument("--venue")
    args = ap.parse_args()

    if args.race_key and args.rank_key:
        targets = [(args.race_key, args.rank_key)]
    elif args.date and args.venue:
        if args.action != "approve":
            print(json.dumps({"ok": False, "message": "場単位は承認のみ対応です"},
                             ensure_ascii=False))
            return 2
        targets = _proposals_for_venue(args.date, args.venue)
        if not targets:
            print(json.dumps({"ok": False, "n_ok": 0, "n_ng": 0, "results": [],
                              "message": "対象の入稿案がありません"}, ensure_ascii=False))
            return 0
    else:
        print(json.dumps(
            {"ok": False, "message": "--race-key/--rank-key か --date/--venue が必要です"},
            ensure_ascii=False))
        return 2

    out = _run(args.action, targets)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
