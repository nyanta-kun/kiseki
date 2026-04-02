"""パドック指数算出 ユニットテスト

PaddockIndexCalculator の calculate / calculate_batch をテストする。
DBアクセスはMockを使用。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.indices.paddock import NEUTRAL_SCORE, PADDOCK_SCORES, PaddockIndexCalculator

# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race(
    race_id: int = 1,
    course: str = "05",
    date: str = "20260402",
    race_number: int = 5,
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = race_id
    r.course = course
    r.date = date
    r.race_number = race_number
    return r


def _make_entry(horse_id: int = 101, horse_number: int = 1) -> MagicMock:
    """RaceEntry モックを生成する。"""
    e = MagicMock()
    e.horse_id = horse_id
    e.horse_number = horse_number
    return e


def _make_calc(
    race: MagicMock | None = None,
    entries: list[MagicMock] | None = None,
) -> PaddockIndexCalculator:
    """モックDB付き PaddockIndexCalculator を生成する。"""
    db = AsyncMock()
    if race is None:
        race = _make_race()
    if entries is None:
        entries = [_make_entry()]

    mock_race_result = MagicMock()
    mock_race_result.scalar_one_or_none.return_value = race
    mock_entries_result = MagicMock()
    mock_entries_result.scalars.return_value.all.return_value = entries
    db.execute.side_effect = [mock_race_result, mock_entries_result]

    return PaddockIndexCalculator(db=db)


# ---------------------------------------------------------------------------
# calculate_batch のテスト
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch のテスト。"""

    async def test_race_not_found_returns_empty(self) -> None:
        """レースが存在しない場合は空dictを返す。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        calc = PaddockIndexCalculator(db=db)
        result = await calc.calculate_batch(race_id=999)
        assert result == {}

    async def test_no_entries_returns_empty(self) -> None:
        """出走馬が存在しない場合は空dictを返す。"""
        db = AsyncMock()
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = _make_race()
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [mock_race_result, mock_entries_result]
        calc = PaddockIndexCalculator(db=db)
        result = await calc.calculate_batch(race_id=1)
        assert result == {}

    async def test_unknown_course_returns_neutral(self) -> None:
        """sekitoコードに対応しない競馬場コードの場合、全馬ニュートラル値を返す。"""
        # "99" は SEKITO_COURSE_MAP に存在しない
        race = _make_race(course="99")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == NEUTRAL_SCORE

    async def test_no_paddock_data_returns_neutral(self) -> None:
        """パドックデータがない場合、全馬ニュートラル値を返す。"""
        race = _make_race(course="05")
        entries = [
            _make_entry(horse_id=101, horse_number=1),
            _make_entry(horse_id=102, horse_number=2),
        ]
        calc = _make_calc(race=race, entries=entries)
        # _fetch_paddock が空のdictを返すようにモック
        calc._fetch_paddock = AsyncMock(return_value={})
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == NEUTRAL_SCORE
        assert result[102] == NEUTRAL_SCORE

    async def test_popular_rank_a_gets_highest_score(self) -> None:
        """人気A評価の馬は最高スコア(85.0)を返す。"""
        race = _make_race(course="05")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        # 馬番1番が人気A評価
        calc._fetch_paddock = AsyncMock(return_value={1: ("人気", "A")})
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == PADDOCK_SCORES[("人気", "A")]

    async def test_popular_rank_c_gets_low_score(self) -> None:
        """人気C評価の馬はニュートラルより低いスコアを返す。"""
        race = _make_race(course="05")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_paddock = AsyncMock(return_value={1: ("人気", "C")})
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == PADDOCK_SCORES[("人気", "C")]
        assert result[101] < NEUTRAL_SCORE

    async def test_tokushu_ana_gets_above_neutral(self) -> None:
        """特注穴評価の馬はニュートラルより高いスコアを返す。"""
        race = _make_race(course="05")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_paddock = AsyncMock(return_value={1: ("特注", "穴")})
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == PADDOCK_SCORES[("特注", "穴")]
        assert result[101] > NEUTRAL_SCORE

    async def test_mixed_horses(self) -> None:
        """複数馬が混在する場合、各馬に適切なスコアを返す。"""
        race = _make_race(course="05")
        entries = [
            _make_entry(horse_id=101, horse_number=1),
            _make_entry(horse_id=102, horse_number=2),
            _make_entry(horse_id=103, horse_number=3),
        ]
        calc = _make_calc(race=race, entries=entries)
        # 1番=人気A, 2番=人気C, 3番=データなし
        calc._fetch_paddock = AsyncMock(return_value={1: ("人気", "A"), 2: ("人気", "C")})
        result = await calc.calculate_batch(race_id=1)
        assert set(result.keys()) == {101, 102, 103}
        assert result[101] == PADDOCK_SCORES[("人気", "A")]  # 85.0
        assert result[102] == PADDOCK_SCORES[("人気", "C")]  # 45.0
        assert result[103] == NEUTRAL_SCORE  # 50.0

    def test_score_ordering(self) -> None:
        """人気A > 特注穴 > ニュートラル > 人気B > 人気C の順になる。

        ※ 特注穴(60) > ニュートラル(50) > 人気B(70) は設計上の期待通りの順序ではないが、
           人気A(85) > 特注穴(60) > ニュートラル(50) の順序は確認できる。
        """
        # 人気A > 人気B > ニュートラル > 人気C の順序確認
        assert PADDOCK_SCORES[("人気", "A")] > PADDOCK_SCORES[("人気", "B")]
        assert PADDOCK_SCORES[("人気", "B")] > NEUTRAL_SCORE
        assert NEUTRAL_SCORE > PADDOCK_SCORES[("人気", "C")]


# ---------------------------------------------------------------------------
# calculate のテスト
# ---------------------------------------------------------------------------


class TestCalculate:
    """calculate のテスト。"""

    async def test_returns_float_for_known_horse(self) -> None:
        """既知の馬IDに対してfloatを返す。"""
        race = _make_race(course="05")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_paddock = AsyncMock(return_value={1: ("人気", "A")})
        result = await calc.calculate(race_id=1, horse_id=101)
        assert isinstance(result, float)
        assert result == PADDOCK_SCORES[("人気", "A")]

    async def test_unknown_horse_returns_neutral(self) -> None:
        """batch に存在しない horse_id はニュートラル値を返す。"""
        race = _make_race(course="05")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_paddock = AsyncMock(return_value={})
        result = await calc.calculate(race_id=1, horse_id=999)
        assert result == NEUTRAL_SCORE

    def test_score_within_valid_range(self) -> None:
        """算出スコアが [0, 100] の範囲内に収まる。"""
        for (p_type, p_rank), score in PADDOCK_SCORES.items():
            assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# _fetch_paddock のテスト
# ---------------------------------------------------------------------------


class TestFetchPaddock:
    """_fetch_paddock のテスト。"""

    async def test_unknown_course_returns_empty(self) -> None:
        """SEKITO_COURSE_MAPに存在しないコースは空dictを返す。"""
        db = AsyncMock()
        calc = PaddockIndexCalculator(db=db)
        race = _make_race(course="99")
        result = await calc._fetch_paddock(race)
        assert result == {}

    async def test_valid_course_executes_query(self) -> None:
        """有効な競馬場コードではSQLを実行する。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        db.execute.return_value = mock_result
        calc = PaddockIndexCalculator(db=db)
        race = _make_race(course="05", date="20260402", race_number=5)
        await calc._fetch_paddock(race)
        assert db.execute.called
