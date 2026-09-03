#!/usr/bin/env python3
"""夜間レビューの**所見**を Discord のレポートchへ送る（2026-09-04 新設）。

    python scripts/notify_triage.py --day 2026-09-03 --url https://... [--dry-run]

## なぜ足したか

所見（`{day}.triage.md`＝今夜やること／蓄積中／検証候補）は 2026-08-30 に
「Discord へは何も送らない・ページを更新するだけ」とした。その結果
**レポートchには一度も何も届かず**（`.notified.json` が存在しないことで確認）、
夜間分析そのものが止まっていると読まれた。2026-09-04 にユーザー判断で
**毎晩1通出す**へ戻した。

## 🔴 `notify_issues.py`（課題）とは別物

    notify_issues … VPS 00:10。異常の有無。**Mac が寝ていても必ず届く**
    notify_triage … Mac 00:12。Claude の所見。Mac が寝ていた夜は出ない

役割が違うので統合しない。片方が欠けた夜があることに意味がある
（所見が無い＝Mac が寝ていた、と読める）。

## 🔴 `issue_list` の「単日の成績数字を載せない」規則は掛けない

あの規則は**課題**（次の行動）の通知に掛けたもので、所見は
「§4 の累積 ROI が逆転している」のように**累積の数字を根拠に据える**のが仕事。
締め出すと所見が意味を失う。代わりに長さだけ関数で担保する。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.notify.issue_list import (                     # noqa: E402
    SAFE_LIMIT, last_sent_day, remember)

NIGHTLY = REPO / "data" / "analysis" / "nightly"
STATE = NIGHTLY / ".notified.json"
KIND = "triage"


def build(day: str, triage: str, url: str = "") -> str:
    """通知本文。**SAFE_LIMIT を超えないことを保証する**。

    🔴 切り詰めたことは必ず本文に残す。黙って切ると
       「載っていない＝無い」と読まれる（`issue_list.render` と同じ作法）。
    """
    head = f"📝 所見 {day}"
    foot = f"📊 <{url}>" if url else ""
    body = (triage or "").strip() or "（所見なし）"
    text = "\n".join(x for x in (head, "", body, foot) if x)
    if len(text) <= SAFE_LIMIT:
        return text
    note = "⚠️ 長すぎるため省略しました → リンク先を参照"
    room = SAFE_LIMIT - len(head) - len(note) - len(foot) - 8
    return "\n".join(x for x in (head, "", body[:max(room, 0)].rstrip(),
                                 note, foot) if x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="")
    ap.add_argument("--url", default="", help="HTML レポートの URL（末尾に付ける）")
    ap.add_argument("--dry-run", action="store_true", help="送らずに本文を出す")
    ap.add_argument("--force", action="store_true", help="本日送信済みでも送る")
    a = ap.parse_args()

    day = a.day or date.today().isoformat()
    src = NIGHTLY / f"{day}.triage.md"
    if not src.exists():
        print(f"[notify_triage] {src} がありません（所見が未生成）")
        return 1

    body = build(day, src.read_text(encoding="utf-8"), url=a.url)
    if a.dry_run:
        print(body)
        print(f"\n[dry-run] {len(body)}文字")
        return 0

    if not a.force and last_sent_day(STATE, KIND) == day:
        print("[notify_triage] 送信せず（本日送信済み）")
        return 0

    from src.notify.discord import send      # 送信するときだけ読む
    if not send(body, channel="review"):
        print("[notify_triage] ⚠️ Discord への送信に失敗")
        return 1
    # 指紋は使わない（所見は毎晩必ず変わる）。日だけ覚えて二重投稿を止める。
    remember(STATE, KIND, day, day)
    print(f"[notify_triage] 送信 {len(body)}文字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
