"""Discord 通知を「運用中のランクだけ」に絞ることの回帰テスト（2026-08-14）。

## 背景（実際に届いた誤通知）

`netkeirin_settings.enabled` は**入稿を止めるだけ**で、ライブ判定・picks_history への
記録・Discord 通知は動き続けていた。そのため 9H1（enabled=false）の不的中通知が
毎レース届いていた（ユーザー指摘）。kiseki Web は同じフラグで非表示にしていたので
**Discord だけが食い違っていた**。

⚠️ 採点と picks_history への記録は**止めない**。記録は正直に残し、
   「見せるかどうか」だけをこのフラグで決める。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import rank_visibility as rv  # noqa: E402


def _rows(keys):
    return [{"rank_key": k} for k in keys]


def test_disabled_ranks_are_prefixed():
    with patch.object(rv, "get_connection") as gc:
        gc.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = \
            _rows(["9H1", "7H2", "_global"])
        assert rv.disabled_rank_names() == {"RANK_9H1", "RANK_7H2"}


def test_global_row_is_not_a_rank():
    """`_global` は全体ON/OFFの特殊行。ランクとして扱わない。"""
    with patch.object(rv, "get_connection") as gc:
        gc.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = \
            _rows(["_global"])
        assert rv.disabled_rank_names() == set()


def test_fails_open_when_the_db_is_unreadable():
    """🔴 読めなければ**何も止めない**。

    fail-closed にすると、DB の不調で通知が全部消えたまま誰も気づけない。
    """
    with patch.object(rv, "get_connection", side_effect=RuntimeError("boom")):
        assert rv.disabled_rank_names() == set()
        assert rv.is_operating("RANK_9H1") is True


def test_is_operating_uses_the_given_set():
    off = {"RANK_9H1"}
    assert rv.is_operating("RANK_9H1", off) is False
    assert rv.is_operating("RANK_9C", off) is True
    assert rv.is_operating(None, off) is True


def test_notifiers_filter_by_the_shared_predicate():
    """🔴 通知側が共通判定を使っていること（自前で書き直すとまた食い違う）。"""
    for rel in ("scripts/notify_race_result_wt.py", "scripts/notify_results_wt.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "disabled_rank_names" in src, f"{rel} が運用中判定を使っていない"
