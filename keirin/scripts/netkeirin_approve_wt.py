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
    STATUS_DELETED,
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


def _cancelable(date: str, venue_name: str | None) -> list[tuple[str, str]]:
    """取消できる下書きを (race_key, rank_key) で返す（発走順）。

    `venue_name` が None ならその日の**全場**。取消の対象は
    **まだ生きているもの**＝入稿案（proposed）と公開待ち（submitted）の両方。
    承認と違って `proposed` に限らないのは、netkeirin へ出した下書きも
    「下書き」として消したいから。

    🔴 **必ず日付で絞る。** `date` を外すと過去分まで巻き込む。
    🔴 **取消済み（deleted）は除く。** 論理削除なので行は残っており、
       含めると「N件取消しました」の N が実態より多く出る。
    """
    ymd = date.replace("-", "")
    sql = ("SELECT race_key, rank_key FROM netkeirin_submissions "
           "WHERE race_key LIKE ? AND COALESCE(status, 'submitted') <> ? ")
    params: list = [f"{ymd}%", STATUS_DELETED]
    if venue_name:
        sql += "AND venue_name = ? "
        params.append(venue_name)
    sql += "ORDER BY venue_name, race_no"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(r["race_key"], r["rank_key"]) for r in rows]


def _run(action: str, targets: list[tuple[str, str]], force: bool = False) -> dict:
    results = []
    for race_key, rank_key in targets:
        try:
            if action == "approve":
                ok, message = approve_and_submit(race_key, rank_key)
            else:
                # force は取消専用。netkeirin 側を触らず記録だけ実態へ合わせる。
                ok, message = cancel_submission(race_key, rank_key, force=force)
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
    ap.add_argument(
        "--all", dest="all_venues", action="store_true",
        help="取消専用。--date で指定した日の**全場・全件**を対象にする")
    ap.add_argument(
        "--force", action="store_true",
        help="取消専用。netkeirin 側の削除をあきらめて記録だけ取消にする"
             "（netkeirin で先に消してしまい記録が残ったときの最後の手段）")
    args = ap.parse_args()
    if args.force and args.action != "cancel":
        print(json.dumps({"ok": False, "message": "--force は cancel 専用です"},
                         ensure_ascii=False))
        return 2
    if args.all_venues and args.action != "cancel":
        print(json.dumps({"ok": False, "message": "--all は cancel 専用です"},
                         ensure_ascii=False))
        return 2
    if args.all_venues and not args.date:
        # 🔴 日付なしの全件取消は絶対に通さない（過去分まで消える）。
        print(json.dumps({"ok": False, "message": "--all には --date が必要です"},
                         ensure_ascii=False))
        return 2

    if args.race_key and args.rank_key:
        targets = [(args.race_key, args.rank_key)]
    elif args.date and (args.venue or args.all_venues):
        # 2026-08-12: 取消も場単位・全件を受け付けるようにした（ユーザー要望）。
        # 元は「まとめて消す事故を避ける」ため承認のみに絞っていた。
        # 事故防止は**画面側の二段確認と件数表示**に移し、ここでは
        # 🔴 **日付必須**（`--all` は `--date` 無しでは通さない）で範囲を縛る。
        if args.action == "approve":
            if args.all_venues:
                print(json.dumps({"ok": False, "message": "--all は cancel 専用です"},
                                 ensure_ascii=False))
                return 2
            targets = _proposals_for_venue(args.date, args.venue)
            empty_msg = "対象の入稿案がありません"
        else:
            targets = _cancelable(args.date, args.venue if args.venue else None)
            empty_msg = "取消できる下書きがありません"
        if not targets:
            print(json.dumps({"ok": False, "n_ok": 0, "n_ng": 0, "results": [],
                              "message": empty_msg}, ensure_ascii=False))
            return 0
    else:
        print(json.dumps(
            {"ok": False,
             "message": "--race-key/--rank-key か --date/--venue（取消は --date/--all も可）が必要です"},
            ensure_ascii=False))
        return 2

    out = _run(args.action, targets, force=args.force)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
