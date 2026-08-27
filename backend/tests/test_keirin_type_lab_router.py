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
