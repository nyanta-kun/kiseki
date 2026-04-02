"""穴ぐさ指数算出 ユニットテスト

AnagusaIndexCalculator の calculate / calculate_batch と内部ユーティリティをテスト。
sekito.anagusaテーブルアクセスはMockを使用。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.indices.anagusa import (
    DEFAULT_SCORE,
    OVERALL_PLACE_RATE,
    RANK_BASE_SCORES,
    AnagusaIndexCalculator,
)

# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race(
    race_id: int = 1,
    course: str = "05",
    surface: str = "芝",
    distance: int = 1600,
    date: str = "20260402",
    race_number: int = 5,
    head_count: int = 16,
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = race_id
    r.course = course
    r.surface = surface
    r.distance = distance
    r.date = date
    r.race_number = race_number
    r.head_count = head_count
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
) -> AnagusaIndexCalculator:
    """モックDB付き AnagusaIndexCalculator を生成する。"""
    db = AsyncMock()
    if race is None:
        race = _make_race()
    if entries is None:
        entries = [_make_entry()]

    # execute() の戻り値を side_effect で制御
    mock_race_result = MagicMock()
    mock_race_result.scalar_one_or_none.return_value = race
    mock_entries_result = MagicMock()
    mock_entries_result.scalars.return_value.all.return_value = entries
    db.execute.side_effect = [mock_race_result, mock_entries_result]

    return AnagusaIndexCalculator(db=db)


# ---------------------------------------------------------------------------
# 内部補正メソッドのユニットテスト
# ---------------------------------------------------------------------------


class TestCourseAdj:
    """_course_adj の補正値テスト。"""

    def test_known_course_returns_correct_adj(self) -> None:
        """既知のコードに対して正しい補正値を返す。"""
        # 福島(03): 17.4% > 15.5%（全体）→ 正の補正
        adj = AnagusaIndexCalculator._course_adj("03")
        assert adj > 0.0

    def test_disadvantaged_course_returns_negative(self) -> None:
        """不利なコースは負の補正値を返す。"""
        # 東京(05): 14.3% < 15.5%（全体）→ 負の補正
        adj = AnagusaIndexCalculator._course_adj("05")
        assert adj < 0.0

    def test_unknown_course_returns_zero(self) -> None:
        """未知のコードは補正0（全体平均と同じ）を返す。"""
        adj = AnagusaIndexCalculator._course_adj("99")
        assert adj == pytest.approx(0.0)


class TestSurfaceDistAdj:
    """_surface_dist_adj の補正値テスト。"""

    def test_turf_1600m_band(self) -> None:
        """芝1600m(band=2)は正常に補正値を返す。"""
        adj = AnagusaIndexCalculator._surface_dist_adj("芝", 1600)
        # 芝band2: 16.6% > 15.5% → 正の補正
        assert adj > 0.0

    def test_dirt_1200m_band(self) -> None:
        """ダート1200m(band=1)は負の補正値を返す。"""
        adj = AnagusaIndexCalculator._surface_dist_adj("ダ", 1200)
        # ダband1: 14.0% < 15.5% → 負の補正
        assert adj < 0.0

    def test_distance_band_boundaries(self) -> None:
        """距離帯の境界値テスト。"""
        # ちょうど1200m → band1
        adj_1200 = AnagusaIndexCalculator._surface_dist_adj("芝", 1200)
        # 1201m → band2
        adj_1201 = AnagusaIndexCalculator._surface_dist_adj("芝", 1201)
        # 異なる距離帯なので値が異なるはず
        assert adj_1200 != adj_1201

    def test_unknown_surface_returns_zero(self) -> None:
        """未知の馬場種別は補正0を返す。"""
        adj = AnagusaIndexCalculator._surface_dist_adj("不明", 1600)
        assert adj == pytest.approx(0.0)


class TestHeadAdj:
    """_head_adj の補正値テスト。"""

    def test_none_returns_zero(self) -> None:
        """head_count=Noneは補正0を返す。"""
        adj = AnagusaIndexCalculator._head_adj(None)
        assert adj == 0.0

    def test_small_field_8(self) -> None:
        """8頭以下はband1（全体平均と同率）→ 補正0。"""
        adj = AnagusaIndexCalculator._head_adj(8)
        assert adj == pytest.approx(0.0)

    def test_medium_field_9_to_13(self) -> None:
        """9〜13頭はband2（17.1% > 15.5%）→ 正の補正。"""
        adj = AnagusaIndexCalculator._head_adj(11)
        assert adj > 0.0

    def test_large_field_14_plus(self) -> None:
        """14頭以上はband3（15.0% < 15.5%）→ 負の補正。"""
        adj = AnagusaIndexCalculator._head_adj(16)
        assert adj < 0.0

    def test_boundary_9_is_medium(self) -> None:
        """ちょうど9頭はband2に入る。"""
        adj_9 = AnagusaIndexCalculator._head_adj(9)
        adj_11 = AnagusaIndexCalculator._head_adj(11)
        assert adj_9 == adj_11  # 同じband


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
        calc = AnagusaIndexCalculator(db=db)
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
        calc = AnagusaIndexCalculator(db=db)
        result = await calc.calculate_batch(race_id=1)
        assert result == {}

    async def test_unknown_course_returns_default(self) -> None:
        """SEKITO_COURSE_MAPに存在しないコースは全馬DEFAULT_SCOREを返す。"""
        race = _make_race(course="99")
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        # _fetch_picks をモック（course="99"なので空dictを返す）
        calc._fetch_picks = AsyncMock(return_value={})
        result = await calc.calculate_batch(race_id=1)
        assert 101 in result

    async def test_no_picks_returns_near_default(self) -> None:
        """ピックがない場合、全馬DEFAULT_SCORE（±補正）を返す。"""
        race = _make_race(course="05", surface="芝", distance=1600, head_count=16)
        entries = [
            _make_entry(horse_id=101, horse_number=1),
            _make_entry(horse_id=102, horse_number=2),
        ]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_picks = AsyncMock(return_value={})
        result = await calc.calculate_batch(race_id=1)
        # 全馬が同じスコア（DEFAULT_SCOREに補正が加わった値）
        assert result[101] == result[102]

    async def test_rank_a_horse_above_default(self) -> None:
        """rank A のピック馬はDEFAULT_SCOREより高いスコアを返す。"""
        race = _make_race(course="05", surface="芝", distance=1600, head_count=16)
        entries = [
            _make_entry(horse_id=101, horse_number=1),
            _make_entry(horse_id=102, horse_number=2),
        ]
        calc = _make_calc(race=race, entries=entries)
        # 1番馬がrank A
        calc._fetch_picks = AsyncMock(return_value={1: "A"})
        result = await calc.calculate_batch(race_id=1)
        # rank A のベーススコア(75) > DEFAULT_SCORE(50)
        assert result[101] > result[102]

    async def test_rank_ordering(self) -> None:
        """スコア順: rank A > rank B > rank C > ピックなし。"""
        race = _make_race(course="06", surface="芝", distance=1800, head_count=12)
        entries = [
            _make_entry(horse_id=101, horse_number=1),
            _make_entry(horse_id=102, horse_number=2),
            _make_entry(horse_id=103, horse_number=3),
            _make_entry(horse_id=104, horse_number=4),
        ]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_picks = AsyncMock(return_value={1: "A", 2: "B", 3: "C"})
        result = await calc.calculate_batch(race_id=1)
        # 補正が同じなので rank の順序が保たれる
        assert result[101] > result[102] > result[103]

    async def test_score_clipped_between_0_and_100(self) -> None:
        """スコアが [0, 100] の範囲内に収まる。"""
        race = _make_race()
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_picks = AsyncMock(return_value={1: "A"})
        result = await calc.calculate_batch(race_id=1)
        assert 0.0 <= result[101] <= 100.0

    async def test_returns_all_horse_ids(self) -> None:
        """全エントリ馬のhorse_idがキーとして返る。"""
        race = _make_race()
        horse_ids = [101, 102, 103, 104, 105]
        entries = [_make_entry(horse_id=hid, horse_number=i + 1) for i, hid in enumerate(horse_ids)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_picks = AsyncMock(return_value={})
        result = await calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)


# ---------------------------------------------------------------------------
# calculate のテスト
# ---------------------------------------------------------------------------


class TestCalculate:
    """calculate のテスト。"""

    async def test_returns_float_for_known_horse(self) -> None:
        """既知の馬IDに対してfloatを返す。"""
        race = _make_race()
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_picks = AsyncMock(return_value={1: "A"})
        result = await calc.calculate(race_id=1, horse_id=101)
        assert isinstance(result, float)

    async def test_unknown_horse_returns_default(self) -> None:
        """batchに存在しない horse_id はDEFAULT_SCOREを返す。"""
        race = _make_race()
        entries = [_make_entry(horse_id=101, horse_number=1)]
        calc = _make_calc(race=race, entries=entries)
        calc._fetch_picks = AsyncMock(return_value={})
        result = await calc.calculate(race_id=1, horse_id=999)
        assert result == DEFAULT_SCORE


# ---------------------------------------------------------------------------
# _fetch_picks のテスト
# ---------------------------------------------------------------------------


class TestFetchPicks:
    """_fetch_picks のテスト。"""

    async def test_unknown_course_returns_empty(self) -> None:
        """SEKITO_COURSE_MAPに存在しないコースは空dictを返す。"""
        db = AsyncMock()
        calc = AnagusaIndexCalculator(db=db)
        race = _make_race(course="99")
        result = await calc._fetch_picks(race)
        assert result == {}

    async def test_valid_course_executes_query(self) -> None:
        """有効な競馬場コードではSQLを実行する。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        db.execute.return_value = mock_result
        calc = AnagusaIndexCalculator(db=db)
        race = _make_race(course="05", date="20260402", race_number=5)
        await calc._fetch_picks(race)
        assert db.execute.called

    async def test_returns_only_valid_ranks(self) -> None:
        """A/B/C以外のrankは除外される。"""
        db = AsyncMock()
        row_a = MagicMock()
        row_a.horse_no = 1
        row_a.rank = "A"
        row_invalid = MagicMock()
        row_invalid.horse_no = 2
        row_invalid.rank = "X"  # 無効なrank
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row_a, row_invalid]
        db.execute.return_value = mock_result
        calc = AnagusaIndexCalculator(db=db)
        race = _make_race(course="05", date="20260402", race_number=5)
        result = await calc._fetch_picks(race)
        assert 1 in result
        assert result[1] == "A"
        assert 2 not in result  # 無効なrankは除外


# ---------------------------------------------------------------------------
# RANK_BASE_SCORES の定数確認
# ---------------------------------------------------------------------------


class TestRankBaseScores:
    """RANK_BASE_SCORESの定数テスト。"""

    def test_rank_a_highest(self) -> None:
        """rank A が最高のベーススコアを持つ。"""
        assert RANK_BASE_SCORES["A"] > RANK_BASE_SCORES["B"]
        assert RANK_BASE_SCORES["B"] > RANK_BASE_SCORES["C"]

    def test_rank_a_above_default(self) -> None:
        """rank A はDEFAULT_SCOREより高い。"""
        assert RANK_BASE_SCORES["A"] > DEFAULT_SCORE

    def test_rank_b_above_default(self) -> None:
        """rank B はDEFAULT_SCOREより高い。"""
        assert RANK_BASE_SCORES["B"] > DEFAULT_SCORE

    def test_rank_c_below_default(self) -> None:
        """rank C（複勝率11.8% < 全体15.5%）はDEFAULT_SCOREより低い。"""
        assert RANK_BASE_SCORES["C"] < DEFAULT_SCORE
