"""看板穴埋めが「埋められなかった」ことを見えるようにする（2026-08-29 新設）。

🔴 発端: 型ラボへ移行して既存ランクを全て `enabled=false` にした 2026-08-29、
   `netkeirin_submit_wt.py` は何もせず 0 で終わるようになった。穴埋めは
   終了コードで数えていたため **1件も入稿していないのに「成功29件」**と記録し、
   看板レース 23R のうち 15R が商品なしのまま流れた。
   ログにも Discord にも「埋まらなかった」が出ないのが問題の本体。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "submit_marquee_wt.py"
BODY = SRC.read_text(encoding="utf-8")


def test_成功は終了コードではなく行の有無で数える():
    """🔴 `_has_submission` を通さずに `done.append` してはいけない。"""
    i = BODY.index("elif p.returncode == 0")
    block = BODY[i:i + 700]
    assert "_has_submission(" in block, (
        "終了コードだけで成功を数えています。ランクが無効なとき子プロセスは"
        "何もせず 0 で終わるので、成功件数が水増しされます")
    assert block.index("_has_submission(") < block.index("done.append"), \
        "行の有無を確かめる前に成功へ数えています"


def test_埋まらなかったレースを別枠で持つ():
    assert "unfilled" in BODY, "埋まらなかったレースの入れ物がありません"
    assert "埋まらなかった" in BODY, "完了ログに件数が出ていません"


def test_未充足の通知はsystemチャンネルへ承認制でも出す():
    """🔴 これは売上通知ではなく**障害通知**。承認制で止めない。"""
    i = BODY.index("def _notify_unfilled(")
    block = BODY[i:BODY.index("\ndef ", i + 10)]
    assert 'channel="system"' in block, "未充足の通知先が system ではありません"
    assert "_approval_required" not in block, (
        "承認制で未充足の通知まで止めています。売り物が出ていないことは"
        "承認制かどうかと無関係に知らせること")


def test_未充足の通知は失敗しても処理を落とさない():
    i = BODY.index("def _notify_unfilled(")
    block = BODY[i:BODY.index("\ndef ", i + 10)]
    assert "try:" in block and "except" in block


def test_通知の呼び出しがdry_runで走らない():
    tree = ast.parse(BODY)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_notify_unfilled"]
    assert calls, "_notify_unfilled が呼ばれていません"
    i = BODY.index("_notify_unfilled(date, unfilled)")
    assert "if not args.dry_run:" in BODY[i - 120:i], \
        "dry-run でも Discord へ出そうとしています"
