"""スピード指数算出 ユニットテスト

DB接続不要のユニットテストと、SQLAlchemy Session をモックした統合テスト。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.importers.race_importer import _finish_time_to_decimal, _last3f_to_decimal
from src.indices.speed import SpeedIndexCalculator
from src.utils.constants import BASE_WEIGHT, SPEED_INDEX_MEAN

# ---------------------------------------------------------------------------
# ユーティリティ: _finish_time_to_decimal / _last3f_to_decimal
# ---------------------------------------------------------------------------


class TestFinishTimeConversion:
    """SEレコードのタイム単位変換テスト。"""

    def test_typical_1600m(self) -> None:
        """1600m 標準タイム: 934 (0.1秒単位) → 93.4秒"""
        assert _finish_time_to_decimal(934) == Decimal("93.4")

    def test_typical_2400m(self) -> None:
        """2400m 標準タイム: 1440 → 144.0秒"""
        assert _finish_time_to_decimal(1440) == Decimal("144.0")

    def test_sprint_1000m(self) -> None:
        """1000m: 580 → 58.0秒"""
        assert _finish_time_to_decimal(580) == Decimal("58.0")

    def test_none_returns_none(self) -> None:
        assert _finish_time_to_decimal(None) is None

    def test_zero_returns_none(self) -> None:
        assert _finish_time_to_decimal(0) is None

    def test_negative_returns_none(self) -> None:
        assert _finish_time_to_decimal(-1) is None


class TestLast3FConversion:
    """上がり3F単位変換テスト。"""

    def test_typical(self) -> None:
        """336 → 33.6秒"""
        assert _last3f_to_decimal(336) == Decimal("33.6")

    def test_fast(self) -> None:
        """330 → 33.0秒"""
        assert _last3f_to_decimal(330) == Decimal("33.0")

    def test_none_returns_none(self) -> None:
        assert _last3f_to_decimal(None) is None

    def test_zero_returns_none(self) -> None:
        assert _last3f_to_decimal(0) is None


# ---------------------------------------------------------------------------
# SpeedIndexCalculator._weighted_average
# ---------------------------------------------------------------------------


class TestWeightedAverage:
    """加重平均テスト（staticメソッド相当を直接テスト）。"""

    def test_empty_returns_mean(self) -> None:
        calc = SpeedIndexCalculator(db=AsyncMock())
        assert calc._weighted_average([]) == SPEED_INDEX_MEAN

    def test_single_score(self) -> None:
        calc = SpeedIndexCalculator(db=AsyncMock())
        assert calc._weighted_average([60.0]) == 60.0

    def test_decay_recent_weighted_more(self) -> None:
        """最新レース（先頭）の重みが高いことを確認。"""
        calc = SpeedIndexCalculator(db=AsyncMock())
        # scores[0]=70 (最新), scores[1]=50 (古い)
        result = calc._weighted_average([70.0, 50.0])
        # w0=1.0, w1=0.8 → (70*1.0 + 50*0.8) / 1.8 = 110/1.8 ≈ 61.1
        expected = round((70.0 * 1.0 + 50.0 * 0.8) / 1.8, 1)
        assert result == expected

    def test_uniform_scores(self) -> None:
        """全スコアが同値なら加重平均も同値。"""
        calc = SpeedIndexCalculator(db=AsyncMock())
        assert calc._weighted_average([55.0, 55.0, 55.0]) == 55.0


# ---------------------------------------------------------------------------
# SpeedIndexCalculator._single_race_speed_score
# ---------------------------------------------------------------------------


def _make_result(finish_time: float, finish_position: int = 1, abnormality: int = 0) -> MagicMock:
    r = MagicMock()
    r.finish_time = Decimal(str(finish_time))
    r.finish_position = finish_position
    r.abnormality_code = abnormality
    return r


def _make_race(
    course: str = "05", distance: int = 1600, surface: str = "芝", condition: str = "良"
) -> MagicMock:
    r = MagicMock()
    r.course = course
    r.distance = distance
    r.surface = surface
    r.condition = condition
    return r


def _make_entry(weight_carried: float = 55.0) -> MagicMock:
    e = MagicMock()
    e.weight_carried = Decimal(str(weight_carried))
    return e


class TestSingleRaceSpeedScore:
    """_single_race_speed_score のユニットテスト。"""

    def _calc(self) -> SpeedIndexCalculator:
        calc = SpeedIndexCalculator(db=AsyncMock())
        # 基準タイム: 平均 93.0 秒, σ 2.0 秒 をキャッシュに注入
        calc._std_time_cache[("05", 1600, "芝", "良")] = (93.0, 2.0)
        return calc

    def test_average_horse_gets_mean(self) -> None:
        """基準タイムちょうどなら指数 ≈ 50（斤量補正なし）。"""
        calc = self._calc()
        result = calc._single_race_speed_score(
            _make_result(93.0), _make_race(), _make_entry(BASE_WEIGHT)
        )
        assert result == pytest.approx(SPEED_INDEX_MEAN, abs=0.1)

    def test_fast_horse_gets_above_mean(self) -> None:
        """基準より1σ速い (91.0秒) → 指数 ≈ 60。"""
        calc = self._calc()
        result = calc._single_race_speed_score(
            _make_result(91.0), _make_race(), _make_entry(BASE_WEIGHT)
        )
        assert result == pytest.approx(60.0, abs=0.1)

    def test_slow_horse_gets_below_mean(self) -> None:
        """基準より1σ遅い (95.0秒) → 指数 ≈ 40。"""
        calc = self._calc()
        result = calc._single_race_speed_score(
            _make_result(95.0), _make_race(), _make_entry(BASE_WEIGHT)
        )
        assert result == pytest.approx(40.0, abs=0.1)

    def test_weight_correction_heavy(self) -> None:
        """重い斤量（60kg）は補正でタイムが加算され指数が下がる。"""
        calc = self._calc()
        score_base = calc._single_race_speed_score(
            _make_result(93.0), _make_race(), _make_entry(55.0)
        )
        score_heavy = calc._single_race_speed_score(
            _make_result(93.0), _make_race(), _make_entry(60.0)
        )
        assert score_heavy < score_base  # 5kg分の不利

    def test_weight_correction_light(self) -> None:
        """軽い斤量（52kg）は補正でタイムが減算され指数が上がる。"""
        calc = self._calc()
        score_base = calc._single_race_speed_score(
            _make_result(93.0), _make_race(), _make_entry(55.0)
        )
        score_light = calc._single_race_speed_score(
            _make_result(93.0), _make_race(), _make_entry(52.0)
        )
        assert score_light > score_base

    def test_scratch_returns_none(self) -> None:
        """除外馬（abnormality_code=1）は None を返す。"""
        calc = self._calc()
        result = calc._single_race_speed_score(
            _make_result(93.0, abnormality=1), _make_race(), _make_entry()
        )
        assert result is None

    def test_no_finish_time_returns_none(self) -> None:
        """タイムなし（finish_time=None）は None を返す。"""
        calc = self._calc()
        r = _make_result(93.0)
        r.finish_time = None
        result = calc._single_race_speed_score(r, _make_race(), _make_entry())
        assert result is None

    def test_no_std_time_returns_none(self) -> None:
        """基準タイムが算出不能（σ=0）は None を返す。"""
        calc = SpeedIndexCalculator(db=AsyncMock())
        calc._std_time_cache[("05", 1600, "芝", "良")] = (0.0, 0.0)
        result = calc._single_race_speed_score(_make_result(93.0), _make_race(), _make_entry())
        assert result is None

    def test_index_clipped_at_100(self) -> None:
        """超高速タイム → 100 でクリップ。"""
        calc = self._calc()
        result = calc._single_race_speed_score(
            _make_result(40.0), _make_race(), _make_entry(BASE_WEIGHT)
        )
        assert result == 100.0

    def test_index_clipped_at_0(self) -> None:
        """超低速タイム → 0 でクリップ。"""
        calc = self._calc()
        result = calc._single_race_speed_score(
            _make_result(200.0), _make_race(), _make_entry(BASE_WEIGHT)
        )
        assert result == 0.0


# ---------------------------------------------------------------------------
# SpeedIndexCalculator.calculate_batch (モック DB)
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch の動作テスト（DB モック使用）。"""

    def _build_calc_with_past_data(
        self, horse_ids: list[int], past_scores: dict[int, list[float]]
    ) -> SpeedIndexCalculator:
        """モックDBと過去データを持つ Calculator を返す。"""
        db = AsyncMock()

        # races.id=1 のレースを返す
        mock_race = MagicMock()
        mock_race.id = 1
        mock_race.date = "20260322"
        mock_race.course = "05"
        mock_race.distance = 1600
        mock_race.surface = "芝"
        mock_race.condition = "良"

        # race_entries: horse_id ごとにエントリを生成
        entries = []
        for hid in horse_ids:
            e = MagicMock()
            e.horse_id = hid
            entries.append(e)

        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = mock_race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = entries
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = SpeedIndexCalculator(db=db)
        calc._std_time_cache[("05", 1600, "芝", "良")] = (93.0, 2.0)

        # _get_past_results_batch をモックして past_scores から直接スコアを返す
        async def mock_batch(horse_ids_arg, before_date, exclude_race_id):
            result = {}
            for hid in horse_ids_arg:
                rows = []
                for score_time in past_scores.get(hid, []):
                    row = MagicMock()
                    row.RaceResult.horse_id = hid
                    row.RaceResult.finish_time = Decimal(str(score_time))
                    row.RaceResult.finish_position = 1
                    row.RaceResult.abnormality_code = 0
                    row.Race.course = "05"
                    row.Race.distance = 1600
                    row.Race.surface = "芝"
                    row.Race.condition = "良"
                    row.RaceEntry.weight_carried = Decimal("55.0")
                    rows.append(row)
                result[hid] = rows
            return result

        calc._get_past_results_batch = mock_batch
        return calc

    async def test_returns_all_horse_ids(self) -> None:
        """全エントリ馬のhorse_idがキーとして返る。"""
        horse_ids = [101, 102, 103]
        past_scores = {101: [91.0], 102: [93.0], 103: [95.0]}
        calc = self._build_calc_with_past_data(horse_ids, past_scores)
        result = await calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)

    async def test_no_past_data_returns_mean(self) -> None:
        """過去データなし → SPEED_INDEX_MEAN を返す。"""
        calc = self._build_calc_with_past_data([101], {})
        result = await calc.calculate_batch(race_id=1)
        assert result[101] == SPEED_INDEX_MEAN

    async def test_relative_ordering(self) -> None:
        """速い馬ほど高い指数になることを確認。"""
        horse_ids = [101, 102]
        # 101は速い(91秒), 102は遅い(95秒)
        past_scores = {101: [91.0], 102: [95.0]}
        calc = self._build_calc_with_past_data(horse_ids, past_scores)
        result = await calc.calculate_batch(race_id=1)
        assert result[101] > result[102]
