"""地方競馬オッズインポーター ユニットテスト

DB接続不要。ChihouOddsImporter および _parse_odds_value の単体テスト。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.importers.chihou_odds_importer import ChihouOddsImporter, _parse_odds_value

# ---------------------------------------------------------------------------
# _parse_odds_value
# ---------------------------------------------------------------------------


class TestParseOddsValue:
    """オッズ文字列変換テスト。"""

    def test_parse_odds_value_conversion(self) -> None:
        """O1 のオッズ値 "0022" → 2.2"""
        assert _parse_odds_value("0022") == pytest.approx(2.2)

    def test_zero_string_returns_none(self) -> None:
        """"0000"（無投票）は None を返す。"""
        assert _parse_odds_value("0000") is None

    def test_non_digit_returns_none(self) -> None:
        """"----"（発売前取消）は None を返す。"""
        assert _parse_odds_value("----") is None

    def test_9999_returns_max_odds(self) -> None:
        """"9999"（999.9倍以上）は 999.9 を返す。"""
        assert _parse_odds_value("9999") == pytest.approx(999.9)


# ---------------------------------------------------------------------------
# ChihouOddsImporter（DBセッションをモック）
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    """DBセッションのモックを返す（非同期対応）。"""
    db = AsyncMock()
    db.execute.return_value = AsyncMock()
    db.execute.return_value.fetchall.return_value = []
    return db


class TestChihouOddsImporter:
    """ChihouOddsImporter の単体テスト。"""

    async def test_import_empty_records(self, mock_db: AsyncMock) -> None:
        """空リストで stats {"saved": 0, "errors": 0, "race_ids": []} を返す。"""
        importer = ChihouOddsImporter(db=mock_db)
        stats = await importer.import_records([])

        assert stats["saved"] == 0
        assert stats["errors"] == 0
        assert stats["race_ids"] == []

    async def test_skips_unknown_rec_id(self, mock_db: AsyncMock) -> None:
        """rec_id="RA" のレコードは無視され saved=0 のまま。"""
        importer = ChihouOddsImporter(db=mock_db)
        stats = await importer.import_records([{"rec_id": "RA", "data": "RA1dummy"}])

        assert stats["saved"] == 0
        assert stats["errors"] == 0
