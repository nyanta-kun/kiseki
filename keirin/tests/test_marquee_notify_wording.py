"""看板レース穴埋めの Discord 文言に対する回帰テスト（2026-08-14）。

## 背景（実際に起きた誤読）

承認制（`netkeirin_settings._global.require_approval`）のとき、穴埋めの子プロセスは
`propose_only=True` で走るので、作られるのは netkeirin へ出ていない
**入稿案（status='proposed'）**。ところが通知だけが

    [netkeirin自動入稿] 2026-08-14 看板レース: 成功12件

と固定文言だったため、直前に出る `[netkeirin入稿案]` と矛盾し
**「承認制なのに自動入稿されている」ように見えた**（ユーザー指摘）。
DB を見ると12件とも `proposed` で、挙動は正しく誤っていたのは文言だけだった。

## 何を守るか

承認制のときは **Discord へ1通も出さない**こと（ユーザー判断 2026-08-14）。
文言を直すだけでは足りない——出しているのは入稿ではなく入稿案なので、
そもそもこの経路から通知する必要がない。判定は入稿側と同じ
`netkeirin_submit_wt._approval_required()` を使うこと（別実装にすると再びズレる）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import submit_marquee_wt  # noqa: E402


def _capture(approval: bool) -> list[str]:
    sent: list[str] = []
    with patch("scripts.netkeirin_submit_wt._approval_required", return_value=approval), \
         patch("src.notify.discord.send", side_effect=lambda c, channel=None: sent.append(c)):
        submit_marquee_wt._notify_summary("2026-08-14", ["立川3R(7C)", "松山5R(9C)"], [])
    return sent


def test_approval_mode_sends_nothing():
    """🔴 承認制のときは Discord へ**1通も出さない**こと。

    出しているのは入稿ではなく入稿案なので、「自動入稿」の通知が届くと
    承認制が効いていないように読める（ユーザー指摘の本体）。
    入稿案の存在は承認催促の通知と `/keirin/review` が伝える。
    """
    assert _capture(True) == [], "承認制なのに Discord へ通知している"


def test_auto_mode_still_notifies():
    sent = _capture(False)
    assert len(sent) == 1, f"自動入稿では1通送ること: {len(sent)}通"
    msg = sent[0]
    assert "自動入稿" in msg
    assert "成功2件" in msg


def test_uses_the_submitter_approval_predicate():
    """🔴 判定を自前で書き直していないこと（別実装にするとまた食い違う）。"""
    src = (REPO / "scripts" / "submit_marquee_wt.py").read_text(encoding="utf-8")
    assert "_approval_required" in src
    # ⚠️ 単に "require_approval" を禁止すると、列名に触れたコメントでも落ちる。
    #    禁止したいのは**自前で引き直すこと**なので SQL の形で見る。
    assert "SELECT require_approval" not in src, (
        "承認制の判定を自前の SQL で書いている。"
        " netkeirin_submit_wt._approval_required() を使うこと")
