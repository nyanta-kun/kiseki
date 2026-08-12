"""入稿締切（発走15分前）の keirin 側の回帰テスト。

固定するのは2点だけ:

1. **正本から束縛していること**（秒数や判定をここで定義していない）
2. **入稿バッチと承認/取消の両方で締切を見ていること**
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.submit_window import (  # noqa: E402
    SUBMIT_DEADLINE_SEC, is_closed, seconds_until_deadline,
)

NOW = 1_700_000_000.0


def test_binds_the_canonical_value():
    assert SUBMIT_DEADLINE_SEC == 15 * 60


@pytest.mark.parametrize("mins,expected", [(60, False), (16, False), (15, True), (0, True)])
def test_is_closed(mins, expected):
    assert is_closed(NOW + mins * 60, NOW) is expected


def test_unknown_start_time_is_not_closed():
    assert is_closed(None, NOW) is False
    assert seconds_until_deadline(None, NOW) is None


def test_deadline_is_not_redefined_here():
    """🔴 秒数や判定を keirin 側で定義していないこと（二重管理の禁止）。

    写した瞬間に「画面は押せるのに API が拒む」を作れる。
    """
    src = (REPO / "src" / "submit_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SUBMIT_DEADLINE_SEC":
                    assert isinstance(node.value, ast.Attribute), (
                        "SUBMIT_DEADLINE_SEC を keirin 側で直接定義しています。"
                        "正本（backend/src/services/keirin_submission_window.py）を"
                        "束縛すること")
        # 判定関数を自前で書いていないこと
        if isinstance(node, ast.FunctionDef):
            assert node.name not in ("is_closed", "seconds_until_deadline"), (
                f"{node.name} を keirin 側で再実装しています")


def test_submit_batch_uses_the_deadline():
    """入稿バッチが「発走済み」ではなく「締切超過」で外していること。"""
    src = (REPO / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert "_load_closed_races" in src
    fn = src[src.index("def _load_closed_races("):src.index("def _load_candidates(")]
    assert "is_closed" in fn, "締切判定を使っていません（発走時刻の直接比較に戻っています）"
    assert "<= now" not in fn, "発走時刻を直接比較しています（締切15分前が効きません）"


def test_marquee_fill_uses_the_deadline():
    src = (REPO / "scripts" / "submit_marquee_wt.py").read_text(encoding="utf-8")
    assert "from src.submit_window import is_closed" in src
    assert 'int(r["start_at"]) <= now_ts' not in src, (
        "穴埋めが発走時刻を直接比較しています（締切15分前が効きません）")


def test_approve_script_blocks_closed_races():
    """🔴 場単位・全件の一括操作は **keirin 側でしか**締切に当たらない。

    kiseki の API はレース単位しか見ないので、ここが抜けると
    「まとめて入稿」で締切超過分まで netkeirin へ投げることになる。
    """
    src = (REPO / "scripts" / "netkeirin_approve_wt.py").read_text(encoding="utf-8")
    assert "_closed_race_keys" in src
    run = src[src.index("def _run("):]
    assert "closed" in run, "_run が締切を見ていません"
    # force（記録だけの取消）は締切に関係なく通す
    assert "if force else" in run or "not force" in run, (
        "force 取消まで締切で塞いでいます。netkeirin を触らない経路なので通すこと")
