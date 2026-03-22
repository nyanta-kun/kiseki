"""ローテーション指数算出 ユニットテスト

DB接続不要のユニットテストと、SQLAlchemy Session をモックした統合テスト。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.indices.rotation import (
    DEFAULT_SCORE,
    RotationIndexCalculator,
    _interval_score,
    _position_bonus,
    _time_bonus,
)
from src.utils.constants import SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# ユーティリティ: _interval_score
# ---------------------------------------------------------------------------


class TestIntervalScore:
    """間隔スコア変換テスト。"""

    def test_extreme_short_7days(self) -> None:
        """7日以下（超過酷ローテ）→ 20"""
        assert _interval_score(7) == 20.0

    def test_extreme_short_1day(self) -> None:
        """1日（最短）→ 20"""
        assert _interval_score(1) == 20.0

    def test_chuno_1week_8days(self) -> None:
        """8日（中1週境界）→ 40"""
        assert _interval_score(8) == 40.0

    def test_chuno_1week_13days(self) -> None:
        """13日（中1週上限）→ 40"""
        assert _interval_score(13) == 40.0

    def test_chuno_2week_14days(self) -> None:
        """14日（中2週境界）→ 60"""
        assert _interval_score(14) == 60.0

    def test_ideal_21days(self) -> None:
        """21日（理想ローテ下限）→ 80"""
        assert _interval_score(21) == 80.0

    def test_ideal_35days(self) -> None:
        """35日（理想ローテ上限）→ 80"""
        assert _interval_score(35) == 80.0

    def test_two_months_36days(self) -> None:
        """36日（2ヶ月以内境界）→ 70"""
        assert _interval_score(36) == 70.0

    def test_three_months_57days(self) -> None:
        """57日（3ヶ月以内境界）→ 55"""
        assert _interval_score(57) == 55.0

    def test_half_year_84days(self) -> None:
        """84日（半年以内境界）→ 40"""
        assert _interval_score(84) == 40.0

    def test_long_rest_168days(self) -> None:
        """168日（長期休養明け境界）→ 30"""
        assert _interval_score(168) == 30.0

    def test_long_rest_365days(self) -> None:
        """365日（長期休養）→ 30"""
        assert _interval_score(365) == 30.0


# ---------------------------------------------------------------------------
# ユーティリティ: _position_bonus
# ---------------------------------------------------------------------------


class TestPositionBonus:
    """着順ボーナス変換テスト。"""

    def test_first_place(self) -> None:
        """1着 → +20"""
        assert _position_bonus(1) == 20.0

    def test_second_place(self) -> None:
        """2着 → +15"""
        assert _position_bonus(2) == 15.0

    def test_third_place(self) -> None:
        """3着 → +10"""
        assert _position_bonus(3) == 10.0

    def test_fourth_place(self) -> None:
        """4着 → +5"""
        assert _position_bonus(4) == 5.0

    def test_fifth_place(self) -> None:
        """5着 → +5"""
        assert _position_bonus(5) == 5.0

    def test_sixth_place(self) -> None:
        """6着以降 → 0"""
        assert _position_bonus(6) == 0.0

    def test_last_place(self) -> None:
        """最下位 → 0"""
        assert _position_bonus(18) == 0.0

    def test_none_returns_zero(self) -> None:
        """着順なし → 0"""
        assert _position_bonus(None) == 0.0


# ---------------------------------------------------------------------------
# ユーティリティ: _time_bonus
# ---------------------------------------------------------------------------


class TestTimeBonus:
    """タイム偏差ボーナス変換テスト。"""

    def test_none_returns_zero(self) -> None:
        """スピードスコアなし → 0"""
        assert _time_bonus(None) == 0.0

    def test_mean_score_returns_zero(self) -> None:
        """スピードスコア=50（平均）→ 0"""
        assert _time_bonus(50.0) == 0.0

    def test_below_mean_returns_zero(self) -> None:
        """スピードスコア<50 → 0"""
        assert _time_bonus(40.0) == 0.0

    def test_above_mean_returns_bonus(self) -> None:
        """スピードスコア=60 → (60-50)/10 * 10 = 10"""
        assert _time_bonus(60.0) == pytest.approx(10.0)

    def test_slightly_above_mean(self) -> None:
        """スピードスコア=55 → (55-50)/10 * 10 = 5"""
        assert _time_bonus(55.0) == pytest.approx(5.0)

    def test_maximum_capped_at_10(self) -> None:
        """スピードスコア=100（最大）→ 上限10"""
        assert _time_bonus(100.0) == 10.0


# ---------------------------------------------------------------------------
# RotationIndexCalculator.calculate: 単一馬テスト（モックDB）
# ---------------------------------------------------------------------------


def _make_mock_race(race_id: int, date: str, course: str = "05") -> MagicMock:
    """テスト用 Race モックを生成する。"""
    r = MagicMock()
    r.id = race_id
    r.date = date
    r.course = course
    r.distance = 1600
    r.surface = "芝"
    r.condition = "良"
    return r


def _make_mock_result(
    horse_id: int,
    finish_position: int | None = 1,
    finish_time: float | None = 93.0,
    abnormality_code: int = 0,
) -> MagicMock:
    """テスト用 RaceResult モックを生成する。"""
    r = MagicMock()
    r.horse_id = horse_id
    r.finish_position = finish_position
    r.finish_time = Decimal(str(finish_time)) if finish_time is not None else None
    r.abnormality_code = abnormality_code
    return r


def _make_row(result: MagicMock, race: MagicMock) -> MagicMock:
    """(RaceResult, Race) タプル相当のモック行を生成する。"""
    row = MagicMock()
    row.RaceResult = result
    row.Race = race
    return row


def _build_calculator(
    target_race: MagicMock,
    past_rows: list[MagicMock],
) -> RotationIndexCalculator:
    """モックDBを持つ RotationIndexCalculator を構築する。

    _get_past_results_for_horse をモックして past_rows を返す。
    _estimate_speed_score が DB を呼ばないよう None を返すようにモック。
    """
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = target_race

    calc = RotationIndexCalculator(db=db)
    calc._get_past_results_for_horse = MagicMock(return_value=past_rows)
    calc._estimate_speed_score = MagicMock(return_value=None)
    return calc


class TestCalculateSingleHorse:
    """calculate（単一馬）のテスト。"""

    def test_first_run_returns_default(self) -> None:
        """初出走（前走なし）→ DEFAULT_SCORE（50.0）"""
        target_race = _make_mock_race(1, "20260322")
        calc = _build_calculator(target_race, [])
        result = calc.calculate(race_id=1, horse_id=101)
        assert result == DEFAULT_SCORE

    def test_ideal_interval_high_score(self) -> None:
        """理想的な間隔（28日）→ interval_score=80（ボーナスなし）= 80.0"""
        target_race = _make_mock_race(1, "20260322")
        prev_race = _make_mock_race(99, "20260223")  # 28日前
        prev_result = _make_mock_result(horse_id=101, finish_position=6)
        rows = [_make_row(prev_result, prev_race)]
        calc = _build_calculator(target_race, rows)
        result = calc.calculate(race_id=1, horse_id=101)
        # interval=80, pos_bonus=0, time_bonus=0 → 80.0
        assert result == 80.0

    def test_extreme_short_interval_low_score(self) -> None:
        """超過酷ローテ（5日）→ interval_score=20（ボーナスなし）= 20.0"""
        target_race = _make_mock_race(1, "20260322")
        prev_race = _make_mock_race(99, "20260317")  # 5日前
        prev_result = _make_mock_result(horse_id=101, finish_position=6)
        rows = [_make_row(prev_result, prev_race)]
        calc = _build_calculator(target_race, rows)
        result = calc.calculate(race_id=1, horse_id=101)
        # interval=20, pos_bonus=0, time_bonus=0 → 20.0
        assert result == 20.0

    def test_long_rest_low_score(self) -> None:
        """長期休養明け（200日）→ interval_score=30（ボーナスなし）= 30.0"""
        target_race = _make_mock_race(1, "20260322")
        prev_race = _make_mock_race(99, "20250904")  # 200日前
        prev_result = _make_mock_result(horse_id=101, finish_position=6)
        rows = [_make_row(prev_result, prev_race)]
        calc = _build_calculator(target_race, rows)
        result = calc.calculate(race_id=1, horse_id=101)
        # interval=30, pos_bonus=0, time_bonus=0 → 30.0
        assert result == 30.0

    def test_first_place_bonus_applied(self) -> None:
        """前走1着ボーナス（+20）が加算される"""
        target_race = _make_mock_race(1, "20260322")
        prev_race = _make_mock_race(99, "20260223")  # 28日前（interval=80）
        prev_result = _make_mock_result(horse_id=101, finish_position=1)
        rows = [_make_row(prev_result, prev_race)]
        calc = _build_calculator(target_race, rows)
        result = calc.calculate(race_id=1, horse_id=101)
        # interval=80, pos_bonus=20, time_bonus=0 → clip(100) = 100.0
        assert result == 100.0

    def test_time_bonus_applied(self) -> None:
        """前走スピードスコア=60 → time_bonus=10 が加算される"""
        target_race = _make_mock_race(1, "20260322")
        prev_race = _make_mock_race(99, "20260223")  # 28日前
        prev_result = _make_mock_result(horse_id=101, finish_position=6)
        rows = [_make_row(prev_result, prev_race)]
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target_race
        calc = RotationIndexCalculator(db=db)
        calc._get_past_results_for_horse = MagicMock(return_value=rows)
        # スピードスコア=60（+10ボーナス）
        calc._estimate_speed_score = MagicMock(return_value=60.0)
        result = calc.calculate(race_id=1, horse_id=101)
        # interval=80, pos_bonus=0, time_bonus=10 → 90.0
        assert result == 90.0

    def test_score_clipped_at_100(self) -> None:
        """全ボーナス満点でも上限100"""
        target_race = _make_mock_race(1, "20260322")
        prev_race = _make_mock_race(99, "20260223")  # 28日前
        prev_result = _make_mock_result(horse_id=101, finish_position=1)
        rows = [_make_row(prev_result, prev_race)]
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target_race
        calc = RotationIndexCalculator(db=db)
        calc._get_past_results_for_horse = MagicMock(return_value=rows)
        # スピードスコア=100（time_bonus=10）
        calc._estimate_speed_score = MagicMock(return_value=100.0)
        result = calc.calculate(race_id=1, horse_id=101)
        # interval=80, pos_bonus=20, time_bonus=10 → clip(110) = 100.0
        assert result == 100.0

    def test_unknown_race_returns_default(self) -> None:
        """存在しない race_id → DEFAULT_SCORE（50.0）"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = RotationIndexCalculator(db=db)
        result = calc.calculate(race_id=9999, horse_id=101)
        assert result == DEFAULT_SCORE

    def test_unknown_horse_returns_default(self) -> None:
        """前走データなし（horse_id未存在）→ DEFAULT_SCORE（50.0）"""
        target_race = _make_mock_race(1, "20260322")
        calc = _build_calculator(target_race, [])
        result = calc.calculate(race_id=1, horse_id=9999)
        assert result == DEFAULT_SCORE


# ---------------------------------------------------------------------------
# RotationIndexCalculator.calculate_batch: バッチテスト（モックDB）
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch の動作テスト（DB モック使用）。"""

    def _build_batch_calc(
        self,
        horse_ids: list[int],
        past_rows_map: dict[int, list[MagicMock]],
        target_date: str = "20260322",
    ) -> RotationIndexCalculator:
        """バッチ用モック Calculator を構築する。"""
        db = MagicMock()

        target_race = _make_mock_race(1, target_date)
        db.query.return_value.filter.return_value.first.return_value = target_race

        entries = []
        for hid in horse_ids:
            e = MagicMock()
            e.horse_id = hid
            entries.append(e)
        db.query.return_value.filter.return_value.all.return_value = entries

        calc = RotationIndexCalculator(db=db)
        calc._get_past_results_batch = MagicMock(return_value=past_rows_map)
        calc._estimate_speed_score = MagicMock(return_value=None)
        return calc

    def test_batch_returns_all_horse_ids(self) -> None:
        """calculate_batch が全馬の horse_id をキーとして返す"""
        horse_ids = [101, 102, 103]
        # 28日前の前走あり
        prev_race = _make_mock_race(99, "20260223")
        past_rows_map = {
            hid: [_make_row(_make_mock_result(hid, finish_position=6), prev_race)]
            for hid in horse_ids
        }
        calc = self._build_batch_calc(horse_ids, past_rows_map)
        result = calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)

    def test_batch_empty_race_id_returns_empty(self) -> None:
        """存在しない race_id → 空dict"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = RotationIndexCalculator(db=db)
        result = calc.calculate_batch(race_id=9999)
        assert result == {}

    def test_batch_no_entries_returns_empty(self) -> None:
        """エントリなしのレース → 空dict"""
        db = MagicMock()
        target_race = _make_mock_race(1, "20260322")
        db.query.return_value.filter.return_value.first.return_value = target_race
        db.query.return_value.filter.return_value.all.return_value = []
        calc = RotationIndexCalculator(db=db)
        result = calc.calculate_batch(race_id=1)
        assert result == {}

    def test_batch_first_run_returns_default(self) -> None:
        """初出走馬（前走なし）は DEFAULT_SCORE を返す"""
        horse_ids = [101]
        calc = self._build_batch_calc(horse_ids, {})
        result = calc.calculate_batch(race_id=1)
        assert result[101] == DEFAULT_SCORE

    def test_batch_score_ordering(self) -> None:
        """理想間隔の馬 > 超過酷ローテの馬 の順でスコアが高い"""
        horse_ids = [101, 102]
        # 101: 28日前（理想）, 102: 5日前（超過酷）
        race_ideal = _make_mock_race(98, "20260223")
        race_extreme = _make_mock_race(99, "20260317")
        past_rows_map = {
            101: [_make_row(_make_mock_result(101, finish_position=6), race_ideal)],
            102: [_make_row(_make_mock_result(102, finish_position=6), race_extreme)],
        }
        calc = self._build_batch_calc(horse_ids, past_rows_map)
        result = calc.calculate_batch(race_id=1)
        assert result[101] > result[102]

    def test_batch_first_place_bonus(self) -> None:
        """前走1着の馬と6着以降の馬で着順ボーナスが正しく反映される"""
        horse_ids = [101, 102]
        prev_race = _make_mock_race(99, "20260223")  # 28日前
        past_rows_map = {
            101: [_make_row(_make_mock_result(101, finish_position=1), prev_race)],
            102: [_make_row(_make_mock_result(102, finish_position=6), prev_race)],
        }
        calc = self._build_batch_calc(horse_ids, past_rows_map)
        result = calc.calculate_batch(race_id=1)
        # 101: interval=80 + pos_bonus=20 = 100, 102: interval=80 + pos_bonus=0 = 80
        assert result[101] == 100.0
        assert result[102] == 80.0
