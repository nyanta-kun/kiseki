"""地方競馬オッズインポーター ユニットテスト

DB接続不要。ChihouOddsImporter および _parse_odds_value の単体テスト。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.importers.chihou_odds_importer import (
    ChihouOddsImporter,
    _parse_odds_value,
    parse_announced_at,
    parse_data_kubun,
)

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


# ---------------------------------------------------------------------------
# 発表時刻・データ区分（2026-09-29）
#
# fetched_at は API が受け取った時刻でしかなく、UmaConn が古いデータを返しても
# 新しく見える。発表時刻を捨てると「配信側の遅れか取得ループの遅れか」を
# 二度と切り分けられなくなるので、取込で必ず残ることを固定する。
# ---------------------------------------------------------------------------


def _o1(*, kubun: str = "1", year: str = "2026", md: str = "0929", announced: str = "09291432") -> str:
    """O1 レコードを組み立てる（単勝 1番 2.2倍・複勝 1番 1.1倍だけ入れる）。"""
    head = "O1" + kubun + "20260929" + year + md + "30" + "01" + "01" + "11" + announced
    assert len(head) == 35
    body = "1212" + "111"  # 登録頭数・出走頭数・発売フラグ（pos 36-42）
    body += "1"  # 複勝着払キー（pos 43）
    win = "01" + "0022" + "01"  # pos 44〜
    win = win.ljust(28 * 8, "0")
    place = "01" + "0011" + "0015" + "01"
    place = place.ljust(28 * 12, "0")
    return (head + body + win + place).ljust(962, " ")


class TestParseAnnouncedAt:
    """発表月日時分 → naive UTC。"""

    def test_converts_jst_to_naive_utc(self) -> None:
        # 2026-09-29 14:32 JST = 05:32 UTC
        assert parse_announced_at(_o1()) == datetime(2026, 9, 29, 5, 32)

    def test_initial_value_is_none(self) -> None:
        """初期値 00000000（未発表）は None。0時0分と取り違えないこと。"""
        assert parse_announced_at(_o1(announced="00000000")) is None

    def test_non_digit_is_none(self) -> None:
        assert parse_announced_at(_o1(announced="        ")) is None

    def test_impossible_date_is_none(self) -> None:
        assert parse_announced_at(_o1(announced="02301200")) is None

    def test_new_year_presale_uses_previous_year(self) -> None:
        """1月2日開催の前日売が 12/31 に発表されたら前年扱いにする。"""
        got = parse_announced_at(_o1(year="2027", md="0102", announced="12311800"))
        assert got == datetime(2026, 12, 31, 9, 0)

    def test_data_kubun(self) -> None:
        assert parse_data_kubun(_o1(kubun="4")) == "4"
        assert parse_data_kubun(_o1(kubun=" ")) is None


class TestImportStoresAnnouncedAt:
    """取込で全行に発表時刻とデータ区分が載る。"""

    async def test_rows_carry_announced_at_and_kubun(self, mock_db: AsyncMock) -> None:
        importer = ChihouOddsImporter(db=mock_db)
        importer._get_race_id = AsyncMock(return_value=42)  # type: ignore[method-assign]

        stats = await importer.import_records([{"rec_id": "O1", "data": _o1(kubun="1")}])

        assert stats["saved"] == 2  # 単勝1 + 複勝1
        rows = mock_db.execute.call_args_list[-1].args[1]
        assert {r["bet_type"] for r in rows} == {"win", "place"}
        for r in rows:
            assert r["announced_at"] == datetime(2026, 9, 29, 5, 32)
            assert r["data_kubun"] == "1"
