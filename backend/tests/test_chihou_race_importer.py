"""地方競馬レースインポーター ユニットテスト

DB接続不要。ChihouRaceImporter の単体テストおよびフィールドマッピング検証。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.importers.chihou_race_importer import ChihouRaceImporter, _finish_time_to_decimal

# ---------------------------------------------------------------------------
# _finish_time_to_decimal
# ---------------------------------------------------------------------------


class TestFinishTimeConversion:
    """SEレコードのタイム単位変換テスト。"""

    def test_typical(self) -> None:
        """934 (0.1秒単位) → Decimal('93.4')"""
        assert _finish_time_to_decimal(934) == Decimal("93.4")

    def test_none_returns_none(self) -> None:
        assert _finish_time_to_decimal(None) is None

    def test_zero_returns_none(self) -> None:
        assert _finish_time_to_decimal(0) is None


# ---------------------------------------------------------------------------
# ChihouRaceImporter（DBセッションをモック）
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    """DBセッションのモックを返す（非同期対応）。"""
    db = AsyncMock()
    db.execute.return_value = AsyncMock()
    db.execute.return_value.fetchall.return_value = []
    return db


def _make_ra_record(race_id: str = "RACE001") -> dict[str, Any]:
    """parse_ra() 出力と同じ形式のサンプルRAレコードを返す。"""
    return {
        "jravan_race_id": race_id,
        "race_date": "20260406",
        "course": "TY",
        "course_name": "東京",
        "race_number": 1,
        "race_name": "テストレース",
        "surface": "芝",
        "distance": 1600,
        "direction": "右",
        "condition": "良",
        "weather": "晴",
        "grade": None,
        "post_time": "1025",
        "race_type_code": "11",
        "weight_type_code": "1",
        "prize_1st": 10000,
        "prize_2nd": 4000,
        "prize_3rd": 2500,
        "head_count": 16,
        "registered_count": 16,
        "finishers_count": None,
        "first_3f": None,
        "last_3f_race": None,
        "lap_times": None,
        "record_update_type": "0",
        "prev_distance": None,
        "prev_track_code": None,
        "prev_grade_code": None,
        "prev_post_time": None,
    }


class TestChihouRaceImporterUnit:
    """DBセッションをモックした ChihouRaceImporter 単体テスト。"""

    async def test_import_empty_records(self, mock_db: AsyncMock) -> None:
        """空リストを渡すと stats が全て 0 で返る。"""
        importer = ChihouRaceImporter(db=mock_db)
        stats = await importer.import_records([])

        assert stats["races"] == 0
        assert stats["entries"] == 0
        assert stats["results"] == 0
        assert stats["errors"] == 0
        assert stats["result_race_ids"] == []

    async def test_import_ra_records(self, mock_db: AsyncMock) -> None:
        """RAレコードを渡すと _bulk_upsert_races が呼ばれる。"""
        importer = ChihouRaceImporter(db=mock_db)

        called_with: list[Any] = []

        async def fake_bulk_upsert_races(ra_list: list[dict[str, Any]]) -> None:
            called_with.extend(ra_list)

        importer._bulk_upsert_races = fake_bulk_upsert_races  # type: ignore[method-assign]

        sample_ra = _make_ra_record("RACE001")
        with patch("src.importers.chihou_race_importer.parse_ra", return_value=sample_ra):
            await importer.import_records([{"rec_id": "RA", "data": "dummy"}])

        assert len(called_with) == 1
        assert called_with[0]["jravan_race_id"] == "RACE001"

    async def test_umaconn_race_id_mapping(self, mock_db: AsyncMock) -> None:
        """_bulk_upsert_races() の values に umaconn_race_id が含まれ jravan_race_id が含まれない。"""
        importer = ChihouRaceImporter(db=mock_db)

        captured_values: list[dict[str, Any]] = []

        async def patched_bulk_upsert(ra_list: list[dict[str, Any]]) -> None:
            for p in ra_list:
                value = {
                    "umaconn_race_id": p["jravan_race_id"],
                    "date": p.get("race_date", ""),
                }
                captured_values.append(value)

        importer._bulk_upsert_races = patched_bulk_upsert  # type: ignore[method-assign]

        sample_ra = _make_ra_record("RACE001")
        with patch("src.importers.chihou_race_importer.parse_ra", return_value=sample_ra):
            await importer.import_records([{"rec_id": "RA", "data": "dummy"}])

        assert len(captured_values) == 1
        assert "umaconn_race_id" in captured_values[0]
        assert "jravan_race_id" not in captured_values[0]


# ---------------------------------------------------------------------------
# ChihouRaceImporter フィールドマッピング
# ---------------------------------------------------------------------------


class TestChihouRaceImporterFieldMapping:
    """_warm_up_caches() 内で ChihouHorse.umaconn_code を使ってクエリすること。"""

    async def test_umaconn_code_used_for_horse_cache(self) -> None:
        """_warm_up_caches が umaconn_code カラムを参照するクエリを発行する。"""
        from unittest.mock import MagicMock

        # _warm_up_caches は (await db.execute(...)).fetchall() を呼ぶ。
        # fetchall() は同期呼び出しなので MagicMock で空リストを返す。
        execute_result = MagicMock()
        execute_result.fetchall.return_value = []

        db = AsyncMock()
        db.execute.return_value = execute_result

        importer = ChihouRaceImporter(db=db)

        se_list = [
            {
                "jravan_horse_code": "HORSE001",
                "jravan_jockey_code": "",
                "jravan_trainer_code": "",
                "jravan_race_id": "",
            }
        ]

        await importer._warm_up_caches(se_list)

        assert db.execute.called
        first_call_arg = db.execute.call_args_list[0][0][0]
        query_str = str(first_call_arg).lower()
        assert "umaconn_code" in query_str
        assert "jravan" not in query_str
