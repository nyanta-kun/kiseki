"""承認（入稿案→送信）と取消の挙動を固定する（2026-08-11）。

## 守ること

1. 承認は**保存済みの買い目をそのまま送る**。再計算しない
   （再計算すると確認画面で見たものと違うものが出て、確認の意味が無くなる）
2. `lines`（展開済み）から組み直さない。`legs`/`marks` の原本を使う
3. 承認は必ず `propose_only=False`（承認したのに送られない、を防ぐ）
4. 取消は netkeirin の下書きを消し、記録は**論理削除**（行は残す）
5. 未送信（proposed）の取消では netkeirin を触らない
"""
from __future__ import annotations

import ast
import inspect
import json

import pytest

import scripts.netkeirin_submit_wt as m
from src.netkeirin_client import BetLeg


def _detail(**over) -> dict:
    d = {
        "total": 1000, "source": "predicted",
        "lines": [{"bet_type": "3連複", "combo": "1=2=3", "stake": 600, "odds": 5.0},
                  {"bet_type": "3連複", "combo": "1=2=4", "stake": 400, "odds": 9.0}],
        "legs": [{"bet_kind": "trio_axis2", "groups": [[1], [2], [3]], "stake": 600},
                 {"bet_kind": "trio_axis2", "groups": [[1], [2], [4]], "stake": 400}],
        "marks": {"1": "◎", "2": "○", "3": "△", "4": "△",
                  "5": "", "6": "", "7": ""},
    }
    d.update(over)
    return d


def test_legs_restored_verbatim():
    legs, marks = m._legs_from_bet_detail(_detail())
    assert legs == [BetLeg("trio_axis2", [[1], [2], [3]], 600),
                    BetLeg("trio_axis2", [[1], [2], [4]], 400)]
    assert marks[1] == "◎" and marks[2] == "○" and marks[3] == "△"


@pytest.mark.parametrize("missing", ["legs", "marks"])
def test_old_format_is_rejected_not_guessed(missing):
    """原本が無い古い形式は**推測で組み直さず**明示的に失敗すること。

    黙って lines から組み直すと、確認画面と違う構造で入稿される。
    """
    d = _detail()
    d.pop(missing)
    with pytest.raises(ValueError, match="古い形式"):
        m._legs_from_bet_detail(d)


def test_approve_does_not_recompute_bets():
    """`approve_and_submit` が買い目を組み直す関数を呼ばないこと。"""
    src = inspect.getsource(m.approve_and_submit)
    for banned in ("_build_tilted_legs", "tilted_stakes", "_legs_for_record",
                   "build_bet_detail", "_load_candidates"):
        assert banned not in src, (
            f"承認時に {banned} を呼んでいます＝買い目を再計算しています。"
            "確認画面で見たものと違うものが入稿されます"
        )
    assert "_legs_from_bet_detail" in src


def test_approve_forces_real_submission():
    """承認は propose_only=False で送ること（承認したのに送られないを防ぐ）。"""
    tree = ast.parse(inspect.getsource(m.approve_and_submit))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "NetkeirinClient":
            kw = {k.arg: k.value for k in node.keywords}
            assert "propose_only" in kw, "承認で propose_only を明示していません"
            v = kw["propose_only"]
            assert isinstance(v, ast.Constant) and v.value is False, (
                "承認が承認制フラグを見ています（承認したのに送られなくなります）"
            )
            found = True
    assert found, "NetkeirinClient の生成が見つかりません"


def test_cancel_is_logical_delete_only():
    """取消で行を物理削除しないこと（bet_detail は再現不能な正本）。"""
    tree = ast.parse(inspect.getsource(m.cancel_submission))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "DELETE FROM" not in sql.upper(), (
        "取消が行を物理削除しています。bet_detail は入稿時の賭け金の唯一の正本で"
        "後から再現できず、消すと ROI・的中率の集計が壊れます"
    )
    assert "UPDATE netkeirin_submissions" in sql
    assert "deleted_at" in sql


def test_cancel_skips_netkeirin_for_unsent_proposal(monkeypatch):
    """未送信（proposed）の取消では netkeirin を一切触らないこと。"""
    calls: list[str] = []

    class _Client:
        def __init__(self, *a, **k):
            calls.append("construct")

        def fetch_item_ids(self):
            calls.append("fetch")
            return {}

        def delete_pick(self, item_id):
            calls.append("delete")
            return True, "ok"

    rows = {"netkeirin_race_id": "PROPOSED", "status": m.STATUS_PROPOSED}
    executed: list[tuple] = []

    class _Conn:
        def execute(self, sql, params=()):
            executed.append((sql, params))

            class _C:
                @staticmethod
                def fetchone():
                    return rows if "SELECT" in sql else None
            return _C()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m, "NetkeirinClient", _Client)
    monkeypatch.setattr(m, "get_connection", lambda: _Conn())
    ok, msg = m.cancel_submission("20260811_13_01", "7C")
    assert ok is True
    assert calls == [], f"未送信なのに netkeirin を触りました: {calls}"
    assert any("UPDATE" in s for s, _ in executed)


def test_load_proposal_filters_by_status():
    """入稿案の取得が status='proposed' で絞ること（送信済みを再送しない）。"""
    tree = ast.parse(inspect.getsource(m._load_proposal))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "status = ?" in sql


def test_bet_detail_carries_legs_and_marks():
    """`build_bet_detail` が原本（legs/marks）を書き出すこと。"""
    legs = [BetLeg("trio_axis2", [[1], [2], [3, 4]], 500)]
    out = json.loads(m.build_bet_detail(legs, "predicted", None,
                                        marks={1: "◎", 2: "○", 3: "△", 4: "△"}))
    assert out["legs"] == [{"bet_kind": "trio_axis2",
                            "groups": [[1], [2], [3, 4]], "stake": 500}]
    assert out["marks"] == {"1": "◎", "2": "○", "3": "△", "4": "△"}
    # 展開済みの lines も従来どおり残る（表示が壊れない）
    assert {x["combo"] for x in out["lines"]} == {"1=2=3", "1=2=4"}
    assert out["total"] == 1000
