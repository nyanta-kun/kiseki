"""_dedup_races_by_id のテスト

差分ファイルに同一 jravan_race_id の複数データ区分が含まれても、バルクupsertの
ON CONFLICT が「cannot affect row a second time」で失敗しないよう、事前に
重複排除（後勝ち）されることを検証する。
"""

from __future__ import annotations

from src.importers.race_importer import _dedup_races_by_id


def test_dedup_keeps_last_occurrence() -> None:
    """同一 jravan_race_id は後勝ち（より新しいデータ区分）で1件に集約。"""
    rows = [
        {"jravan_race_id": "R1", "data_type": "1", "race_condition_code": None},
        {"jravan_race_id": "R1", "data_type": "2", "race_condition_code": "701"},
        {"jravan_race_id": "R2", "data_type": "2", "race_condition_code": "703"},
    ]
    out = _dedup_races_by_id(rows)
    assert len(out) == 2
    by_id = {r["jravan_race_id"]: r for r in out}
    assert by_id["R1"]["data_type"] == "2"
    assert by_id["R1"]["race_condition_code"] == "701"
    assert by_id["R2"]["race_condition_code"] == "703"


def test_dedup_preserves_order() -> None:
    rows = [
        {"jravan_race_id": "R3"},
        {"jravan_race_id": "R1"},
        {"jravan_race_id": "R2"},
        {"jravan_race_id": "R1"},
    ]
    out = _dedup_races_by_id(rows)
    assert [r["jravan_race_id"] for r in out] == ["R3", "R1", "R2"]


def test_dedup_skips_missing_id() -> None:
    rows = [{"jravan_race_id": None}, {"foo": "bar"}, {"jravan_race_id": "R1"}]
    out = _dedup_races_by_id(rows)
    assert [r["jravan_race_id"] for r in out] == ["R1"]


def test_dedup_empty() -> None:
    assert _dedup_races_by_id([]) == []
