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

承認制のときは「自動入稿」「成功」と書かないこと。判定は入稿側と同じ
`netkeirin_submit_wt._approval_required()` を使うこと（別実装にすると再びズレる）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import submit_marquee_wt  # noqa: E402


def _capture(approval: bool) -> str:
    sent: list[str] = []
    with patch("scripts.netkeirin_submit_wt._approval_required", return_value=approval), \
         patch("src.notify.discord.send", side_effect=lambda c, channel=None: sent.append(c)):
        submit_marquee_wt._notify_summary("2026-08-14", ["立川3R(7C)", "松山5R(9C)"], [])
    assert sent, "Discord へ1通も送っていない"
    return sent[0]


def test_approval_mode_does_not_say_auto_submitted():
    msg = _capture(True)
    assert "自動入稿" not in msg, (
        "承認制なのに『自動入稿』と書いている。"
        " 実体は未承認の入稿案で netkeirin には出ていない")
    assert "入稿案" in msg
    assert "成功" not in msg, "承認制で『成功』は誤り（まだ出していない）"
    assert "未承認" in msg, "未承認のままでは出ないことを伝えていない"


def test_auto_mode_keeps_the_original_wording():
    msg = _capture(False)
    assert "自動入稿" in msg
    assert "成功2件" in msg
    assert "入稿案" not in msg


def test_uses_the_submitter_approval_predicate():
    """🔴 判定を自前で書き直していないこと（別実装にするとまた食い違う）。"""
    src = (REPO / "scripts" / "submit_marquee_wt.py").read_text(encoding="utf-8")
    assert "_approval_required" in src
    # ⚠️ 単に "require_approval" を禁止すると、列名に触れたコメントでも落ちる。
    #    禁止したいのは**自前で引き直すこと**なので SQL の形で見る。
    assert "SELECT require_approval" not in src, (
        "承認制の判定を自前の SQL で書いている。"
        " netkeirin_submit_wt._approval_required() を使うこと")
