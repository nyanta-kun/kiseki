"""入稿・取消の締切（発走15分前）判定の回帰テスト。

この判定は **入稿バッチ（keirin）・承認API・Web の確認画面**の3箇所で要る。
値や条件が食い違うと「画面は押せるのに API が拒む」「バッチだけ古い締切で出す」が
静かに起きるので、**正本1つに束ねていること**を機械的に縛る。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.services.keirin_submission_window import (
    SUBMIT_DEADLINE_SEC,
    is_closed,
    seconds_until_deadline,
)

REPO = Path(__file__).resolve().parents[2]
NOW = 1_700_000_000.0


def test_deadline_is_15_minutes():
    assert SUBMIT_DEADLINE_SEC == 15 * 60


@pytest.mark.parametrize("mins_to_start,expected", [
    (60, False),    # 1時間前 → まだ出せる
    (16, False),    # 16分前 → まだ出せる
    (15, True),     # ちょうど15分前 → 締切（境界は「締切の中」）
    (14, True),     # 14分前 → 締切
    (0, True),      # 発走時刻
    (-10, True),    # 発走後
])
def test_is_closed_boundary(mins_to_start, expected):
    assert is_closed(NOW + mins_to_start * 60, NOW) is expected


def test_unknown_start_time_is_not_closed():
    """🔴 発走時刻が取れない行は**締切前**扱い（操作を許す）。

    情報が無いことを理由に商品を落とすと、黙って商品が消える。
    """
    assert is_closed(None, NOW) is False
    assert seconds_until_deadline(None, NOW) is None


def test_garbage_start_time_is_not_closed():
    assert is_closed("あ", NOW) is False


def test_seconds_until_deadline_sign():
    assert seconds_until_deadline(NOW + 20 * 60, NOW) == pytest.approx(5 * 60)
    assert seconds_until_deadline(NOW + 10 * 60, NOW) == pytest.approx(-5 * 60)


def test_canonical_imports_stdlib_only():
    """🔴 keirin は自分の venv からこのファイルを直接読む。

    依存を足すと **Web は無事なまま入稿だけが落ちる**（marquee 正本と同じ制約）。
    """
    src = (REPO / "backend" / "src" / "services"
           / "keirin_submission_window.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        line = line.strip()
        if line.startswith(("import ", "from ")) and "__future__" not in line:
            pytest.fail(f"正本が標準ライブラリ以外を import しています: {line}")


def test_frontend_constant_matches_canonical():
    """確認画面の写しが正本とずれていないこと。

    フロントは表示（ボタンの活殺）のために同じ秒数を持つ。ずれると
    「押せるのに API が 409 を返す」になる。
    """
    tsx = (REPO / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text(encoding="utf-8")
    m = re.search(r"const SUBMIT_DEADLINE_SEC\s*=\s*([0-9*\s]+);", tsx)
    assert m, "ReviewClient.tsx に SUBMIT_DEADLINE_SEC がありません"
    assert eval(m.group(1).strip()) == SUBMIT_DEADLINE_SEC  # noqa: S307 - 数式のみ


def test_approve_and_cancel_are_guarded():
    """承認API が締切を見ていること（見ないと netkeirin で弾かれるだけ）。"""
    src = (REPO / "backend" / "src" / "api" / "keirin_router.py").read_text(encoding="utf-8")
    assert "_closed_races" in src, "承認API が締切判定を呼んでいません"
    # approve / cancel の両方に入っていること
    approve = src[src.index("async def approve_proposal("):src.index("async def cancel_proposal(")]
    cancel = src[src.index("async def cancel_proposal("):]
    assert "_closed_races" in approve, "approve に締切ガードがありません"
    assert "_closed_races" in cancel, "cancel に締切ガードがありません"
    # 🔴 force（記録だけの取消）は締切に関係なく通す＝永久に片付かない行を作らない
    assert "not body.force" in cancel, (
        "force 取消まで締切で塞いでいます。netkeirin を触らない経路なので通すこと")
