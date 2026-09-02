#!/usr/bin/env python3
"""夜間レビューの課題を Discord へ送る（2026-09-02 新設・案A）。

    python scripts/notify_issues.py --day 2026-08-31 --kind anomaly
    python scripts/notify_issues.py --day 2026-08-31 --kind anomaly --dry-run

## なぜ別チャンネルなのか

`nightly_review.sh` は「Discord へは1行の要約とリンクだけ」（2026-08-30・
ユーザー要望）。**それは事実レポートの話**で、ここで送るのは課題
（＝次の行動）なので性質が違う。混ぜると片方が読まれなくなるため
`review` チャンネルを新設して分ける。

## 🔴 既存の夜間チェーンには一切触らない

異常の判定は `nightly_review_type_lab.py` が既に済ませていて、結果は
`data/analysis/nightly/{day}.md` の §1 に `[NG]` として残っている。
**その .md を読むだけ**にしてあるので、レポート生成側の挙動は変わらない
（doc18 の「本番挙動の変更禁止・新機能は opt-in」）。

## 🔴 Mac が寝ていても届く

この経路は VPS 側（`nightly_review.sh` の末尾）で走る。triage は Mac 依存で、
Mac が寝ていると課題が届かなかった。異常だけは Claude を待たずに届く。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.notify.issue_list import (                     # noqa: E402
    EMPTY_DIGEST, build_anomaly_message, digest, remember, render, should_send)

NIGHTLY = REPO / "data" / "analysis" / "nightly"
STATE = NIGHTLY / ".notified.json"

#: §1 の行。`  [NG] 本文` の形（`section_alerts` の `ng()` が作る）。
_NG = re.compile(r"^\s*\[NG\]\s+(.*\S)\s*$")
_OK = re.compile(r"^\s*\[OK\]\s")
_H2 = re.compile(r"^## ")


def parse_alerts(md: str) -> tuple[list[str], int]:
    """§1 から (NG の本文, OK の件数) を取り出す。

    🔴 **§1 の中だけを見る。** 他の節にも `[NG]` が出る余地があるので、
       節の境界（`## `）で必ず打ち切る。
    """
    ng: list[str] = []
    n_ok = 0
    inside = False
    for line in md.splitlines():
        if _H2.match(line):
            if inside:
                break
            inside = "§1" in line
            continue
        if not inside:
            continue
        m = _NG.match(line)
        if m:
            ng.append(m.group(1))
        elif _OK.match(line):
            n_ok += 1
    return ng, n_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="")
    ap.add_argument("--kind", choices=("anomaly",), default="anomaly")
    ap.add_argument("--url", default="", help="HTML レポートの URL（末尾に付ける）")
    ap.add_argument("--dry-run", action="store_true", help="送らずに本文を出す")
    ap.add_argument("--force", action="store_true",
                    help="前夜と同一でも送る（差分抑止を外す）")
    a = ap.parse_args()

    day = a.day or (date.today().isoformat())
    src = NIGHTLY / f"{day}.md"
    if not src.exists():
        print(f"[notify_issues] {src} がありません（夜間レビュー未実行）")
        return 1

    ng, n_ok = parse_alerts(src.read_text(encoding="utf-8"))
    msg = build_anomaly_message(day, ng, url=a.url, n_ok=n_ok)
    body = render(msg)
    fp = digest(msg) if ng else EMPTY_DIGEST

    if a.dry_run:
        print(body)
        print(f"\n[dry-run] NG {len(ng)}件 / OK {n_ok}件 / "
              f"{len(body)}文字 / digest={fp}")
        return 0

    if not a.force and not should_send(STATE, a.kind, fp):
        # 🔴 **不発を無音にしない。** 「異常なし」も「前夜と同じ」も、
        #    ログに残らないと「通知が壊れている」と区別できない。
        why = "異常なし" if not ng else "前夜と同じ内容"
        print(f"[notify_issues] 送信せず（{why}）NG {len(ng)}件")
        return 0

    from src.notify.discord import send      # 送信するときだけ読む
    if not send(body, channel="review"):
        print("[notify_issues] ⚠️ Discord への送信に失敗")
        return 1
    remember(STATE, a.kind, fp, day)
    print(f"[notify_issues] 送信 NG {len(ng)}件 / {len(body)}文字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
