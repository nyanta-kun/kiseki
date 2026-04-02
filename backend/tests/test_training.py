"""調教指数算出 ユニットテスト

TrainingIndexCalculator の calculate / calculate_batch と内部ユーティリティをテスト。
DBアクセスはMockを使用。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.indices.training import (
    NEUTRAL,
    TrainingIndexCalculator,
    _linear_trend,
    _trend_to_score,
    _weight_cond_score,
)

# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race(
    race_id: int = 1,
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    condition: str = "良",
    date: str = "20260402",
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = race_id
    r.course = course
    r.distance = distance
    r.surface = surface
    r.condition = condition
    r.date = date
    return r


def _make_entry(horse_id: int = 101) -> MagicMock:
    """RaceEntry モックを生成する。"""
    e = MagicMock()
    e.horse_id = horse_id
    return e


def _make_row(
    horse_id: int = 101,
    finish_time: float | None = 93.0,
    last_3f: float | None = 34.0,
    weight_change: int | None = 0,
    abnormality_code: int = 0,
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    date: str = "20260322",
) -> MagicMock:
    """(RaceResult, Race) 行モックを生成する。"""
    row = MagicMock()
    row.RaceResult.horse_id = horse_id
    row.RaceResult.finish_time = Decimal(str(finish_time)) if finish_time is not None else None
    row.RaceResult.last_3f = Decimal(str(last_3f)) if last_3f is not None else None
    row.RaceResult.weight_change = weight_change
    row.RaceResult.abnormality_code = abnormality_code
    row.Race.course = course
    row.Race.distance = distance
    row.Race.surface = surface
    row.Race.condition = "良"
    row.Race.date = date
    return row


# ---------------------------------------------------------------------------
# _weight_cond_score のユニットテスト
# ---------------------------------------------------------------------------


class TestWeightCondScore:
    """_weight_cond_score の変換テスト。"""

    def test_none_returns_neutral(self) -> None:
        """体重変化が不明の場合はニュートラルを返す。"""
        assert _weight_cond_score(None) == NEUTRAL

    def test_stable_small_change(self) -> None:
        """±2kg以内は安定スコアを返す。"""
        assert _weight_cond_score(0) == 55.0
        assert _weight_cond_score(2) == 55.0
        assert _weight_cond_score(-2) == 55.0

    def test_moderate_change(self) -> None:
        """±3〜6kgは許容スコアを返す。"""
        assert _weight_cond_score(4) == 52.0
        assert _weight_cond_score(-4) == 52.0

    def test_large_change(self) -> None:
        """±7〜10kgはやや懸念スコアを返す。"""
        assert _weight_cond_score(8) == 45.0
        assert _weight_cond_score(-8) == 45.0

    def test_very_large_change(self) -> None:
        """±11kg以上は大幅変化スコアを返す。"""
        assert _weight_cond_score(12) == 38.0
        assert _weight_cond_score(-12) == 38.0


# ---------------------------------------------------------------------------
# _linear_trend のユニットテスト
# ---------------------------------------------------------------------------


class TestLinearTrend:
    """_linear_trend の線形回帰テスト。"""

    def test_empty_list_returns_zero(self) -> None:
        """空リストは0.0を返す。"""
        assert _linear_trend([]) == 0.0

    def test_single_value_returns_zero(self) -> None:
        """1要素は0.0を返す。"""
        assert _linear_trend([50.0]) == 0.0

    def test_increasing_trend_positive(self) -> None:
        """増加トレンドは正の傾きを返す。"""
        slope = _linear_trend([40.0, 50.0, 60.0])
        assert slope > 0.0

    def test_decreasing_trend_negative(self) -> None:
        """減少トレンドは負の傾きを返す。"""
        slope = _linear_trend([60.0, 50.0, 40.0])
        assert slope < 0.0

    def test_flat_trend_near_zero(self) -> None:
        """一定値では傾き 0.0 を返す。"""
        slope = _linear_trend([50.0, 50.0, 50.0])
        assert slope == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _trend_to_score のユニットテスト
# ---------------------------------------------------------------------------


class TestTrendToScore:
    """_trend_to_score の変換テスト。"""

    def test_zero_slope_returns_neutral(self) -> None:
        """傾き0はNEUTRALを返す。"""
        assert _trend_to_score(0.0) == pytest.approx(NEUTRAL)

    def test_positive_slope_above_neutral(self) -> None:
        """正の傾きはNEUTRALより高い値を返す。"""
        score = _trend_to_score(2.0, scale=2.0)
        assert score > NEUTRAL

    def test_negative_slope_below_neutral(self) -> None:
        """負の傾きはNEUTRALより低い値を返す。"""
        score = _trend_to_score(-2.0, scale=2.0)
        assert score < NEUTRAL

    def test_score_clipped_at_100(self) -> None:
        """スコアは100.0でクリップされる。"""
        score = _trend_to_score(100.0, scale=1.0)
        assert score == 100.0

    def test_score_clipped_at_0(self) -> None:
        """スコアは0.0でクリップされる。"""
        score = _trend_to_score(-100.0, scale=1.0)
        assert score == 0.0


# ---------------------------------------------------------------------------
# TrainingIndexCalculator.calculate のテスト
# ---------------------------------------------------------------------------


class TestCalculate:
    """calculate のテスト。"""

    async def test_race_not_found_returns_neutral(self) -> None:
        """レースが存在しない場合はNEUTRALを返す。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        calc = TrainingIndexCalculator(db=db)
        result = await calc.calculate(race_id=999, horse_id=101)
        assert result == NEUTRAL

    async def test_no_past_results_returns_neutral(self) -> None:
        """過去レース結果がない場合はNEUTRALを返す。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_race()
        db.execute.return_value = mock_result
        calc = TrainingIndexCalculator(db=db)
        calc._fetch_past_results = AsyncMock(return_value={})
        result = await calc.calculate(race_id=1, horse_id=101)
        assert result == NEUTRAL

    async def test_returns_float(self) -> None:
        """正常系ではfloatを返す。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_race()
        db.execute.return_value = mock_result
        calc = TrainingIndexCalculator(db=db)
        rows = [_make_row(horse_id=101) for _ in range(3)]
        calc._fetch_past_results = AsyncMock(return_value={101: rows})
        calc._baseline_cache[("05", 1600, "芝")] = (93.0, 2.0, 100)
        result = await calc.calculate(race_id=1, horse_id=101)
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0


# ---------------------------------------------------------------------------
# TrainingIndexCalculator.calculate_batch のテスト
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch のテスト。"""

    async def test_race_not_found_returns_empty(self) -> None:
        """レースが存在しない場合は空dictを返す。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        calc = TrainingIndexCalculator(db=db)
        result = await calc.calculate_batch(race_id=999)
        assert result == {}

    async def test_no_entries_returns_empty(self) -> None:
        """エントリがない場合は空dictを返す。"""
        db = AsyncMock()
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = _make_race()
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [mock_race_result, mock_entries_result]
        calc = TrainingIndexCalculator(db=db)
        result = await calc.calculate_batch(race_id=1)
        assert result == {}

    async def test_returns_all_horse_ids(self) -> None:
        """全エントリ馬のhorse_idがキーとして返る。"""
        db = AsyncMock()
        race = _make_race()
        entries = [
            _make_entry(horse_id=101),
            _make_entry(horse_id=102),
            _make_entry(horse_id=103),
        ]
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = entries
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = TrainingIndexCalculator(db=db)
        # 過去結果なしでニュートラルを返すようにモック
        calc._fetch_past_results = AsyncMock(return_value={})
        result = await calc.calculate_batch(race_id=1)
        assert set(result.keys()) == {101, 102, 103}

    async def test_no_past_data_returns_neutral_for_all(self) -> None:
        """過去データなしの場合、全馬NEUTRALを返す。"""
        db = AsyncMock()
        race = _make_race()
        entries = [_make_entry(horse_id=101), _make_entry(horse_id=102)]
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = entries
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = TrainingIndexCalculator(db=db)
        calc._fetch_past_results = AsyncMock(return_value={})
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == NEUTRAL
        assert result[102] == NEUTRAL

    async def test_improving_horse_above_neutral(self) -> None:
        """改善傾向にある馬はNEUTRAL以上のスコアを返す。"""
        db = AsyncMock()
        race = _make_race()
        entries = [_make_entry(horse_id=101)]
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = entries
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = TrainingIndexCalculator(db=db)
        # タイムが改善（古→新: 95.0 → 93.0 → 91.0）
        rows = [
            _make_row(horse_id=101, finish_time=91.0, date="20260322"),
            _make_row(horse_id=101, finish_time=93.0, date="20260215"),
            _make_row(horse_id=101, finish_time=95.0, date="20260101"),
        ]
        calc._fetch_past_results = AsyncMock(return_value={101: rows})
        calc._baseline_cache[("05", 1600, "芝")] = (93.0, 2.0, 100)

        result = await calc.calculate_batch(race_id=1)
        # 改善傾向なのでNEUTRAL以上を期待（time_trend_scoreが高い）
        assert result[101] >= NEUTRAL

    async def test_score_within_valid_range(self) -> None:
        """算出スコアが [0, 100] の範囲内に収まる。"""
        db = AsyncMock()
        race = _make_race()
        entries = [_make_entry(horse_id=101)]
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = entries
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = TrainingIndexCalculator(db=db)
        rows = [_make_row(horse_id=101) for _ in range(5)]
        calc._fetch_past_results = AsyncMock(return_value={101: rows})
        calc._baseline_cache[("05", 1600, "芝")] = (93.0, 2.0, 100)

        result = await calc.calculate_batch(race_id=1)
        assert 0.0 <= result[101] <= 100.0


# ---------------------------------------------------------------------------
# TrainingIndexCalculator._compute のテスト
# ---------------------------------------------------------------------------


class TestCompute:
    """_compute のテスト。"""

    def _make_calc(self) -> TrainingIndexCalculator:
        """基準タイムキャッシュを設定済みのCalculatorを返す。"""
        db = AsyncMock()
        calc = TrainingIndexCalculator(db=db)
        calc._baseline_cache[("05", 1600, "芝")] = (93.0, 2.0, 100)
        return calc

    async def test_empty_rows_returns_neutral(self) -> None:
        """行が空の場合はNEUTRALを返す。"""
        calc = self._make_calc()
        race = _make_race()
        assert await calc._compute([], race) == NEUTRAL

    async def test_with_data_returns_float(self) -> None:
        """データがある場合はfloatを返す。"""
        calc = self._make_calc()
        race = _make_race()
        rows = [_make_row(horse_id=101, finish_time=93.0, last_3f=34.0, weight_change=0)]
        result = await calc._compute(rows, race)
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0

    async def test_stable_weight_higher_than_erratic(self) -> None:
        """体重が安定している馬は大幅変化の馬より高いスコアになる。"""
        calc = self._make_calc()
        race = _make_race()
        rows_stable = [_make_row(horse_id=101, finish_time=93.0, weight_change=0)]
        rows_erratic = [_make_row(horse_id=102, finish_time=93.0, weight_change=15)]
        score_stable = await calc._compute(rows_stable, race)
        score_erratic = await calc._compute(rows_erratic, race)
        assert score_stable > score_erratic
