#!/usr/bin/env python3
"""入稿案の承認・取消を行う CLI（2026-08-11 新設）。

確認画面（`/keirin/review`）から webhook 経由で呼ばれる。**結果を JSON で
標準出力へ出す**ので、呼び出し側は成否をその場で画面へ返せる。

    python scripts/netkeirin_approve_wt.py approve --race-key 20260811_13_01 --rank-key 7C
    python scripts/netkeirin_approve_wt.py approve --date 2026-08-11 --venue 前橋
    python scripts/netkeirin_approve_wt.py approve --date 2026-08-11 --all   # その日の全場
    python scripts/netkeirin_approve_wt.py cancel  --race-key 20260811_13_01 --rank-key 7C
    python scripts/netkeirin_approve_wt.py approve --date 2026-08-16 --all --publish  # 入稿して公開
    python scripts/netkeirin_approve_wt.py publish --date 2026-08-16 --all            # 公開だけ

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
    STATUS_PUBLISHED,
    STATUS_SUBMITTED,
    approve_and_submit,
    cancel_submission,
    publish_submissions,
)
from src.database import get_connection  # noqa: E402
from src.submit_window import SUBMIT_DEADLINE_SEC, is_closed  # noqa: E402


def _proposals_for_venue(date: str, venue_name: str | None) -> list[tuple[str, str]]:
    """承認できる入稿案を (race_key, rank_key) で返す（発走順）。

    `date` は YYYY-MM-DD。race_key の先頭8桁が YYYYMMDD。
    `venue_name` が None ならその日の**全場**（2026-08-16 追加）。

    🔴 **必ず日付で絞る。** `date` を外すと過去分の入稿案まで承認してしまう。
    🔴 対象は `proposed` だけ。既に netkeirin へ出したもの（submitted）を
       混ぜると二重入稿になる。
    """
    ymd = date.replace("-", "")
    sql = ("SELECT race_key, rank_key FROM netkeirin_submissions "
           "WHERE race_key LIKE ? AND status = ? ")
    params: list = [f"{ymd}%", STATUS_PROPOSED]
    if venue_name:
        sql += "AND venue_name = ? "
        params.append(venue_name)
    sql += "ORDER BY venue_name, race_no"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
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
    🔴 **公開済み（published）も除く**（2026-08-16）。公開済みに netkeirin の
       `delete` が効くかは仕様に記載が無く未確認で、含めると一括取消のたびに
       必ず失敗する行が混ざって明細が読めなくなる。
    """
    ymd = date.replace("-", "")
    sql = ("SELECT race_key, rank_key FROM netkeirin_submissions "
           "WHERE race_key LIKE ? AND COALESCE(status, 'submitted') NOT IN (?, ?) ")
    params: list = [f"{ymd}%", STATUS_DELETED, STATUS_PUBLISHED]
    if venue_name:
        sql += "AND venue_name = ? "
        params.append(venue_name)
    sql += "ORDER BY venue_name, race_no"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(r["race_key"], r["rank_key"]) for r in rows]


def _publishable(date: str, venue_name: str | None) -> list[tuple[str, str]]:
    """公開できるものを (race_key, rank_key) で返す（発走順）。

    対象は **`proposed`（未入稿）と `submitted`（公開待ち）の両方**。
    🔴 **入稿前のものは「入稿の上で公開」する**（2026-08-16・ユーザー指定の
       ボタン整理）。画面の操作を 入稿 / 取消 / 公開 の3つに畳むための仕様で、
       「公開」を押したときに入稿済かどうかを人が意識しなくてよくする。
       入稿は `_run()` が先に済ませる（承認が通ったものだけ公開する）。
    🔴 公開済み（published）は含めない（二重に押しても害は無いが件数が狂う）。
    🔴 **必ず日付で絞る。** `date` を外すと過去分まで公開してしまう。
    """
    ymd = date.replace("-", "")
    sql = ("SELECT race_key, rank_key FROM netkeirin_submissions "
           "WHERE race_key LIKE ? AND status IN (?, ?) ")
    params: list = [f"{ymd}%", STATUS_PROPOSED, STATUS_SUBMITTED]
    if venue_name:
        sql += "AND venue_name = ? "
        params.append(venue_name)
    sql += "ORDER BY venue_name, race_no"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(r["race_key"], r["rank_key"]) for r in rows]


def _status_of(targets: list[tuple[str, str]], status: str) -> list[tuple[str, str]]:
    """`targets` のうち `status` のものを返す（順序は保つ）。"""
    if not targets:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, rank_key FROM netkeirin_submissions WHERE status = ?",
            (status,),
        ).fetchall()
    hit = {(r["race_key"], r["rank_key"]) for r in rows}
    return [t for t in targets if t in hit]


def _closed_race_keys(targets: list[tuple[str, str]]) -> set[str]:
    """締切（発走15分前）を過ぎたレースの race_key。

    🔴 **ここが実際の関門**。API 側（kiseki）はレース単位しか見ないので、
       場単位・全件の一括操作はここで初めて締切に当たる。
    """
    from datetime import datetime

    keys = sorted({rk.split("#")[0] for rk, _ in targets})
    if not keys:
        return set()
    now = datetime.now().timestamp()
    ph = ",".join("?" * len(keys))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT race_key, start_at FROM wt_races WHERE race_key IN ({ph})",
            keys,
        ).fetchall()
    return {r["race_key"] for r in rows if is_closed(r["start_at"], now)}


#: `message` に並べる失敗理由の上限（画面の1行に収まる範囲）
_MAX_REASONS = 3


def _summarize(results: list[dict]) -> dict:
    """1件ずつの結果を畳んで返す。

    🔴 **`message` を必ず入れること**（2026-08-16 追加）。Web 側は
       `json.message ?? "実行しました"` で埋めるので、ここに `message` が無いと
       **失敗しているのに「成功0件 / 失敗1件: 実行しました」**という自己矛盾した
       表示になり、`results[]` に入っている本当の理由がどこにも出ない。
       実際にこれで「公開ボタンが無反応」に見えた（京王閣12R）。
    """
    n_ok = sum(1 for r in results if r["ok"])
    n_ng = len(results) - n_ok
    if not results:
        message = "対象がありません"
    elif n_ng == 0:
        message = f"{n_ok}件を処理しました"
    else:
        # 失敗の理由をそのまま出す。同じ理由は畳んで件数を添える。
        reasons: dict[str, int] = {}
        for r in results:
            if not r["ok"]:
                reasons[str(r.get("message") or "理由不明")] = (
                    reasons.get(str(r.get("message") or "理由不明"), 0) + 1)
        shown = [f"{m}（{n}件）" if n > 1 else m
                 for m, n in list(reasons.items())[:_MAX_REASONS]]
        if len(reasons) > _MAX_REASONS:
            shown.append(f"ほか{len(reasons) - _MAX_REASONS}種")
        message = " / ".join(shown)
    return {"ok": n_ok == len(results) and bool(results), "message": message,
            "n_ok": n_ok, "n_ng": n_ng, "results": results}


def _run(action: str, targets: list[tuple[str, str]], force: bool = False,
         publish: bool = False, reason: str | None = None) -> dict:
    """承認 / 取消 / 公開を実行して1件ずつの結果を返す。

    `publish=True` を承認に添えると、**承認が通ったものだけ**を続けて公開する
    （画面の「入稿して公開」ボタン）。🔴 承認に失敗したものは公開しない。
    """
    if action == "publish":
        # 🔴 締切超過は netkeirin の画面でも押させない（JS の check_closetime）。
        #    API を直に叩く側で同じ関門を通す。
        closed = _closed_race_keys(targets)
        blocked = [{"race_key": rk, "rank_key": nk, "ok": False,
                    "message": f"発走{SUBMIT_DEADLINE_SEC // 60}分前を過ぎているため操作できません"}
                   for rk, nk in targets if rk.split("#")[0] in closed]
        sendable = [(rk, nk) for rk, nk in targets if rk.split("#")[0] not in closed]
        # 🔴 **入稿前のものは先に入稿してから公開する。** 画面の「公開」は
        #    入稿済かどうかを人に意識させないためのボタンなので、ここで吸収する。
        #    入稿に失敗したものは公開しない（`_run(approve, publish=True)` の規則）。
        not_yet = set(_status_of(sendable, STATUS_PROPOSED))
        via_approve = _run("approve", [t for t in sendable if t in not_yet], publish=True)
        direct = publish_submissions([t for t in sendable if t not in not_yet])
        return _summarize(blocked + via_approve["results"] + direct)

    results = []
    # 🔴 締切を過ぎたレースは netkeirin が受け付けない。**先に落として理由を返す**
    #    （黙って成功扱いにすると、出ていないのに出したことになる）。
    #    `force` は netkeirin を触らず記録だけ直すので締切に関係なく通す。
    closed = set() if force else _closed_race_keys(targets)
    for race_key, rank_key in targets:
        if race_key.split("#")[0] in closed:
            results.append({
                "race_key": race_key, "rank_key": rank_key, "ok": False,
                "message": f"発走{SUBMIT_DEADLINE_SEC // 60}分前を過ぎているため操作できません",
            })
            continue
        try:
            if action == "approve":
                ok, message = approve_and_submit(race_key, rank_key)
            else:
                # force は取消専用。netkeirin 側を触らず記録だけ実態へ合わせる。
                # reason は「なぜ取り消したか」。一覧の「取消」バッジに出る。
                ok, message = cancel_submission(race_key, rank_key, force=force,
                                                reason=reason)
        except Exception as e:  # noqa: BLE001 — 1件の失敗で残りを止めない
            ok, message = False, f"例外: {e}"
        results.append({"race_key": race_key, "rank_key": rank_key,
                        "ok": bool(ok), "message": str(message)})
    if action == "approve" and publish:
        # 承認できたものだけを公開する。**失敗した分は触らない**
        # （公開は不可逆なので、送れていないものを公開扱いにしない）。
        ok_targets = [(r["race_key"], r["rank_key"]) for r in results if r["ok"]]
        published = {(r["race_key"], r["rank_key"]): r
                     for r in publish_submissions(ok_targets)}
        for r in results:
            pub = published.get((r["race_key"], r["rank_key"]))
            if pub is None:
                continue
            # 承認は通ったが公開で失敗＝**全体としては失敗**（画面へ出す）
            r["ok"] = bool(r["ok"] and pub["ok"])
            r["message"] = f"入稿: {r['message']} / 公開: {pub['message']}"
    return _summarize(results)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("approve", "cancel", "publish"))
    ap.add_argument("--race-key")
    ap.add_argument("--rank-key")
    ap.add_argument("--date")
    ap.add_argument("--venue")
    ap.add_argument(
        "--all", dest="all_venues", action="store_true",
        help="--date で指定した日の**全場・全件**を対象にする"
             "（承認は proposed のみ / 取消は生きている下書き全部）")
    ap.add_argument(
        "--publish", action="store_true",
        help="承認専用。入稿が通ったものを続けて**公開**する（公開は不可逆）")
    ap.add_argument(
        "--force", action="store_true",
        help="取消専用。netkeirin 側の削除をあきらめて記録だけ取消にする"
             "（netkeirin で先に消してしまい記録が残ったときの最後の手段）")
    ap.add_argument(
        "--reason",
        help="取消専用。なぜ取り消したか（一覧の「取消」バッジに出る）。"
             "画面のボタンごとの固定文言を想定している")
    args = ap.parse_args()
    if args.reason and args.action != "cancel":
        print(json.dumps({"ok": False, "message": "--reason は cancel 専用です"},
                         ensure_ascii=False))
        return 2
    if args.force and args.action != "cancel":
        print(json.dumps({"ok": False, "message": "--force は cancel 専用です"},
                         ensure_ascii=False))
        return 2
    if args.publish and args.action != "approve":
        # 単体の公開は `publish` アクションを使う（意図を取り違えないため）。
        print(json.dumps({"ok": False, "message": "--publish は approve 専用です"},
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
        if args.action == "publish":
            targets = _publishable(args.date, args.venue if args.venue else None)
            empty_msg = "公開できる入稿がありません"
        elif args.action == "approve":
            # 2026-08-16: 承認も `--all`（その日の全場）を受け付ける（ユーザー要望）。
            # 取消に全件があって承認に無いのは非対称なだけで、事故防止の作法
            # （日付必須・画面の二段確認と件数表示）は取消と同じものが使える。
            targets = _proposals_for_venue(args.date, args.venue if args.venue else None)
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
             "message": "--race-key/--rank-key か --date/--venue か --date/--all が必要です"},
            ensure_ascii=False))
        return 2

    out = _run(args.action, targets, force=args.force, publish=args.publish,
               reason=args.reason)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
