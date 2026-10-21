"""一般競走のレース名を RA 取込で補い、確定前の仮名を NULL に戻さないことの検査。

## なぜ要るか

RA の競走名本題は一般競走（２歳未勝利・３歳以上１勝クラス等）では空。
以前は `race_name` を素直に上書きしていたため、scrape_projected_entries.py が
出馬表確定前に入れた仮名が確定後の RA で NULL に戻り、sekito の画面で
特別競走以外のレース名が出なくなっていた（2026-10-22）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from src.importers.race_importer import RaceImporter, jra_general_race_name


@pytest.mark.parametrize(
    ("type_code", "cond_code", "expected"),
    [
        ("11", "701", "メイクデビュー"),
        ("11", "703", "2歳未勝利"),
        ("11", "005", "2歳1勝クラス"),
        ("12", "702", "3歳未出走"),
        ("13", "010", "3歳以上2勝クラス"),
        ("14", "016", "4歳以上3勝クラス"),
        ("18", "703", "障害3歳以上未勝利"),
        ("19", "999", "オープン"),
    ],
)
def test_general_race_name(type_code, cond_code, expected):
    """表記は kiseki 画面の Race.race_class_label と同じ。"""
    assert jra_general_race_name(type_code, cond_code) == expected


@pytest.mark.parametrize("cond_code", [None, "", "000", "500"])
def test_general_race_name_unknown_condition_is_none(cond_code):
    """条件コード無し・旧式の賞金クラスは名前を作らない。

    race_class_label は賞金から推定するが、推定値をレース名として保存しない。
    """
    assert jra_general_race_name("11", cond_code) is None


def _ra(**over) -> dict:
    base = {
        "jravan_race_id": "2026101805040101",
        "race_date": "20261018",
        "course": "05",
        "course_name": "東京",
        "race_number": 1,
        "race_name": "",
        "surface": "ダ",
        "distance": 1400,
        "race_type_code": "11",
        "race_condition_code": "703",
    }
    base.update(over)
    return base


async def _captured_upsert(ra_list: list[dict]):
    db = AsyncMock()
    captured: list = []

    async def _execute(stmt, *a, **k):
        captured.append(stmt)
        result = AsyncMock()
        result.__iter__ = lambda self: iter([])
        return result

    db.execute = AsyncMock(side_effect=_execute)
    await RaceImporter(db)._bulk_upsert_races(ra_list)
    assert captured, "UPSERT が実行されていない"
    return captured[-1].compile(dialect=postgresql.dialect())


@pytest.mark.asyncio
async def test_empty_ra_name_is_filled_from_codes():
    compiled = await _captured_upsert([_ra()])
    assert "2歳未勝利" in compiled.params.values()


@pytest.mark.asyncio
async def test_special_race_name_is_kept():
    compiled = await _captured_upsert([_ra(race_name="アイビーステークス", race_condition_code="999")])
    values = compiled.params.values()
    assert "アイビーステークス" in values
    assert "オープン" not in values


@pytest.mark.asyncio
async def test_race_name_is_guarded_by_coalesce():
    """名前を作れない RA で、確定前の仮名を NULL に戻さない。"""
    compiled = await _captured_upsert([_ra(race_condition_code=None)])
    assert "coalesce(excluded.race_name" in str(compiled).lower()
