"""未承認の催促を固定する（2026-08-11）。

## 守ること

1. 承認制が OFF のときは**何もしない**（自動入稿されるので催促する相手がいない）
2. **催促だけで自動入稿はしない**（ユーザー指示。未承認は見送り）
3. 発走済みは催促しない（入稿しても売れない）
"""
from __future__ import annotations

import ast
import inspect

import scripts.notify_pending_approvals_wt as m


def test_does_nothing_when_approval_is_off(monkeypatch, capsys):
    monkeypatch.setattr(m, "_approval_required", lambda: False)
    monkeypatch.setattr(m, "send", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("承認制OFFなのに通知しました")))
    assert m.main() == 0
    assert "何もしません" in capsys.readouterr().out


def test_never_submits_automatically():
    """催促スクリプトが入稿系の関数を呼ばないこと。"""
    src = inspect.getsource(m)
    for banned in ("approve_and_submit", "submit_pick", "NetkeirinClient"):
        assert banned not in src, (
            f"催促スクリプトが {banned} を参照しています。"
            "未承認は見送りで、自動入稿はしない仕様です"
        )


def test_excludes_started_races():
    """発走済みを除いていること（入稿しても売れない）。"""
    src = inspect.getsource(m.pending)
    assert "start_at" in src and "now_ts" in src


def test_query_filters_proposed_only():
    tree = ast.parse(inspect.getsource(m.pending))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "status = ?" in sql


def test_message_says_no_auto_submission(monkeypatch):
    """文面で「自動入稿はしない」と伝えること（誤解すると放置される）。"""
    sent: list[str] = []
    monkeypatch.setattr(m, "_approval_required", lambda: True)
    monkeypatch.setattr(m, "pending", lambda d: [
        {"venue_name": "前橋", "race_no": 11, "rank_key": "7A", "start_at": None}])
    monkeypatch.setattr(m, "send", lambda msg, channel=None: sent.append(msg) or True)
    monkeypatch.setattr("sys.argv", ["x", "2026-08-11"])
    assert m.main() == 0
    assert len(sent) == 1
    assert "自動入稿はしません" in sent[0]
    assert m.REVIEW_URL in sent[0]
