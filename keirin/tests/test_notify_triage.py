"""所見通知（`scripts/notify_triage.py`）の整形（2026-09-04 新設）。

🔴 ここで固定するのは「長さ」と「切り詰めたことが本文に残るか」だけ。
   所見の中身（累積 ROI 等）は**締め出さない**——課題通知の
   `PERFORMANCE_TOKENS` 規則は次の行動を書く通知に掛けたもので、
   所見は累積の数字を根拠に据えるのが仕事だから。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "notify_triage", REPO / "scripts" / "notify_triage.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)      # type: ignore[union-attr]
build = _mod.build

from src.notify.issue_list import SAFE_LIMIT      # noqa: E402


def test_見出しと本文とリンクが出る() -> None:
    body = build("2026-09-03", "**今夜やること**\n- なし", url="https://x/y.html")
    assert body.startswith("📝 所見 2026-09-03")
    assert "**今夜やること**" in body
    assert "📊 <https://x/y.html>" in body


def test_累積の数字は締め出さない() -> None:
    # 課題通知とは規則が違う。ここで落とすと所見が根拠を失う。
    body = build("2026-09-03", "- §4 累積 ROI 71.2% ↔ 95.2%", url="")
    assert "ROI 71.2%" in body


def test_所見が空でも本文になる() -> None:
    assert "（所見なし）" in build("2026-09-03", "   \n", url="")


def test_長すぎる所見は切り詰めて理由を残す() -> None:
    body = build("2026-09-03", "あ" * 5000, url="https://x/y.html")
    assert len(body) <= SAFE_LIMIT
    # 🔴 黙って切ると「載っていない＝無い」と読まれる。
    assert "省略しました" in body
    assert "📊 <https://x/y.html>" in body


@pytest.mark.parametrize("url", ["", "https://x/y.html"])
def test_リンクの有無どちらでも上限を守る(url: str) -> None:
    assert len(build("2026-09-03", "い" * 9000, url=url)) <= SAFE_LIMIT
