"""型ラボ API の回帰テスト（2026-08-27）。

ここで固定するのは、壊れると**ページが 500 になって何も見えない**点。
"""
from __future__ import annotations

from datetime import date

from src.api.keirin_type_lab_router import CURRENT_RANK_ORDER, PLAN_ORDER, window


def test_window_returns_date_objects_for_date_columns():
    """🔴 asyncpg は DATE 列へ文字列を渡せない。

    `'str' object has no attribute 'toordinal'` で 500 になる（2026-08-27 に実際に踏んだ）。
    `race_date` と比べる引数は必ず `datetime.date` にすること。
    """
    d1, d2, dd1, dd2 = window("2026-08-01", "2026-08-07")
    assert (d1, d2) == ("2026-08-01", "2026-08-07")
    assert isinstance(dd1, date) and isinstance(dd2, date)
    assert (dd1.isoformat(), dd2.isoformat()) == (d1, d2)


def test_window_defaults_to_the_last_seven_days():
    d1, d2, dd1, dd2 = window(None, "2026-08-07")
    assert d1 == "2026-08-01" and d2 == "2026-08-07"
    assert (dd2 - dd1).days == 6


def test_lists_are_not_empty():
    """表示順と優先順位の手書きリストが空になっていないこと。"""
    assert len(PLAN_ORDER) == 8
    assert CURRENT_RANK_ORDER[0] == "RANK_7H2" and CURRENT_RANK_ORDER[-1] == "RANK_7M1"


def test_each_query_gets_the_parameter_type_its_column_needs():
    """🔴 `race_date` の型がテーブルごとに違う。

        keirin.type_lab_picks.race_date  … DATE     → datetime.date
        keirin.picks_history.race_date   … VARCHAR  → str
        keirin.netkeirin_submissions     … 日付列なし → race_key の先頭8桁（str）

    asyncpg は型を厳格に見るので取り違えると即 500 になる。
    2026-08-27 に**両方向とも**踏んだ（文字列を DATE へ／date を VARCHAR へ）。
    呼び出し側が渡している式を構文で固定する。
    """
    import ast
    import inspect

    from src.api import keirin_type_lab_router as m

    src = inspect.getsource(m.get_type_lab)
    tree = ast.parse(src.lstrip())
    got: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        target = getattr(node.args[0], "id", None)
        if target not in ("_SQL", "_SQL_CURRENT", "_SQL_SOLD"):
            continue
        d = node.args[1]
        if not isinstance(d, ast.Dict):
            continue
        got[target] = {ast.unparse(v) for k, v in zip(d.keys, d.values)
                       if getattr(k, "value", "") in ("d1", "d2")}
    assert got.get("_SQL") == {"dd1", "dd2"}, got.get("_SQL")
    assert got.get("_SQL_CURRENT") == {"d1", "d2"}, got.get("_SQL_CURRENT")
    assert got.get("_SQL_SOLD") == {"d1.replace('-', '')", "d2.replace('-', '')"}, \
        got.get("_SQL_SOLD")
