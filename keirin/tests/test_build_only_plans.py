"""`build_type_lab_picks.py --only-plans`（2026-09-24 新設）。

入稿済みの日に**新しいプランの行だけ**を足すための流し方。素の `save` は
`_drop_stale_plans` で「今回組んだプラン以外の未採点の行」を消すので、
新しいプランの行だけを渡すと**同じレースの他のプランの行を全部消す**。
ここでは、指定したとき掃除も書き込みもそのプランに限られることを固定する。

経緯: 逃げ先頭ライン（`L_lead`）のデプロイが 2026-09-24 の朝の生成（07:11）より
18分遅れ、その日の `L_lead` の行が作られなかった。日全体を組み直すと入稿済みの
買い目まで UPSERT で書き換わるので、`L_lead` だけを足す手段が要った。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location(
        "build_type_lab_picks", REPO / "scripts" / "build_type_lab_picks.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Cur:
    rowcount = 0


class _Conn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        return _Cur()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _row(plan: str, race: str = "20260924_28_02") -> dict:
    return {"race_key": race, "mode": "live", "plan_key": plan}


def test_指定しなければ掃除は従来どおり():
    conn = _Conn()
    _mod()._drop_stale_plans(conn, [_row("C_hit")])
    sql, params = conn.calls[0]
    assert "plan_key IN" not in sql.replace("NOT IN", "")
    assert params == ("20260924_28_02", "live", "C_hit")


def test_指定したら掃除はそのプランだけに限る():
    conn = _Conn()
    _mod()._drop_stale_plans(conn, [_row("L_lead")], {"L_lead"})
    sql, params = conn.calls[0]
    assert "AND plan_key IN (?)" in sql, "他のプランの未採点の行まで消す形になっている"
    assert params == ("20260924_28_02", "live", "L_lead", "L_lead")


def test_指定したら書くのはそのプランの行だけ(monkeypatch):
    m = _mod()
    conn = _Conn()
    monkeypatch.setattr(m, "get_connection", lambda: conn)
    cols = {c: None for c in m.COLS}
    rows = [dict(cols, **_row("C_hit")), dict(cols, **_row("L_lead"))]
    assert m.save(rows, {"L_lead"}) == 1
    inserts = [p for s, p in conn.calls if s.startswith("INSERT INTO type_lab_picks")]
    assert len(inserts) == 1
    assert inserts[0][m.COLS.index("plan_key")] == "L_lead"


def test_知らないプランは受け付けない(monkeypatch):
    import sys

    import pytest

    m = _mod()
    monkeypatch.setattr(sys, "argv", ["x", "--mode", "live", "--only-plans", "L_leed"])
    with pytest.raises(SystemExit):
        m.main()
