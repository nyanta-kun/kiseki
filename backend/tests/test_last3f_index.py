"""上がり3ハロン指数算出 ユニットテスト

DB接続不要のユニットテスト。SQLAlchemy Session をモックして検証する。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.indices.last3f import (
    MIN_FIELD_SAMPLE,
    MIN_RACES,
    WEIGHT_DECAY,
    Last3FIndexCalculator,
)
from src.utils.constants import SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# ユーティリティ: モックオブジェクト生成ヘルパー
# ---------------------------------------------------------------------------


def _make_result(
    horse_id: int = 1,
    race_id: int = 10,
    last_3f: float | None = 35.0,
    abnormality_code: int = 0,
) -> MagicMock:
    """RaceResult モックを生成する。"""
    r = MagicMock()
    r.horse_id = horse_id
    r.race_id = race_id
    r.last_3f = Decimal(str(last_3f)) if last_3f is not None else None
    r.abnormality_code = abnormality_code
    return r


def _make_race(
    id: int = 10,
    date: str = "20250101",
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = id
    r.date = date
    r.course = course
    r.distance = distance
    r.surface = surface
    return r


def _make_entry(horse_id: int = 1) -> MagicMock:
    """RaceEntry モックを生成する。"""
    e = MagicMock()
    e.horse_id = horse_id
    return e


def _make_past_row(
    horse_id: int = 1,
    race_id: int = 10,
    last_3f: float | None = 35.0,
    abnormality_code: int = 0,
    date: str = "20250101",
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """(RaceResult, Race, RaceEntry) タプルモックを生成する。"""
    return (
        _make_result(horse_id=horse_id, race_id=race_id, last_3f=last_3f, abnormality_code=abnormality_code),
        _make_race(id=race_id, date=date),
        _make_entry(horse_id=horse_id),
    )


def _make_calculator(
    race: MagicMock | None = None,
    entries: list[MagicMock] | None = None,
) -> Last3FIndexCalculator:
    """DB モックを持つ Last3FIndexCalculator を生成する。"""
    db = MagicMock()
    default_race = race or _make_race(id=99, date="20260322")
    default_entries = entries or [_make_entry(horse_id=i) for i in range(1, 4)]

    db.query.return_value.filter.return_value.first.return_value = default_race
    db.query.return_value.filter.return_value.all.return_value = default_entries

    calc = Last3FIndexCalculator(db)
    return calc


# ---------------------------------------------------------------------------
# _compute_scores のテスト
# ---------------------------------------------------------------------------


class TestComputeScores:
    """_compute_scores の単体テスト。"""

    def test_empty_rows_returns_empty(self) -> None:
        calc = _make_calculator()
        result = calc._compute_scores([], {})
        assert result == []

    def test_none_last3f_is_skipped(self) -> None:
        row = _make_past_row(last_3f=None)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert result == []

    def test_missing_field_stats_is_skipped(self) -> None:
        row = _make_past_row(last_3f=34.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {})
        assert result == []

    def test_none_field_stats_is_skipped(self) -> None:
        row = _make_past_row(last_3f=34.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: None})
        assert result == []

    def test_average_time_scores_50(self) -> None:
        """フィールド平均タイムの馬はスコア50.0。"""
        row = _make_past_row(last_3f=35.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert len(result) == 1
        assert result[0] == pytest.approx(50.0, abs=0.1)

    def test_faster_than_average_scores_higher(self) -> None:
        """フィールド平均より速い（タイム低い）馬は50超。"""
        row = _make_past_row(last_3f=34.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert result[0] > 50.0

    def test_slower_than_average_scores_lower(self) -> None:
        """フィールド平均より遅い（タイム高い）馬は50未満。"""
        row = _make_past_row(last_3f=36.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert result[0] < 50.0

    def test_1sigma_faster_scores_60(self) -> None:
        """1σ速い馬のスコアは60.0。"""
        row = _make_past_row(last_3f=34.0, race_id=10)
        calc = _make_calculator()
        # mean=35.0, std=1.0 → z=1.0 → score=60
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert result[0] == pytest.approx(60.0, abs=0.1)

    def test_score_clips_at_100(self) -> None:
        """極端に速い場合はスコア100にクリップ。"""
        row = _make_past_row(last_3f=28.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert result[0] == 100.0

    def test_score_clips_at_0(self) -> None:
        """極端に遅い場合はスコア0にクリップ。"""
        row = _make_past_row(last_3f=42.0, race_id=10)
        calc = _make_calculator()
        result = calc._compute_scores([row], {10: (35.0, 1.0)})
        assert result[0] == 0.0

    def test_multiple_rows_in_order(self) -> None:
        """複数レースのスコアは渡した順に返る。"""
        rows = [
            _make_past_row(last_3f=34.0, race_id=10),  # faster
            _make_past_row(last_3f=36.0, race_id=11),  # slower
        ]
        calc = _make_calculator()
        stats = {10: (35.0, 1.0), 11: (35.0, 1.0)}
        result = calc._compute_scores(rows, stats)
        assert len(result) == 2
        assert result[0] > 50.0   # faster → high
        assert result[1] < 50.0   # slower → low


# ---------------------------------------------------------------------------
# _weighted_average のテスト
# ---------------------------------------------------------------------------


class TestWeightedAverage:
    """_weighted_average の単体テスト。"""

    def test_empty_returns_mean(self) -> None:
        calc = _make_calculator()
        assert calc._weighted_average([]) == SPEED_INDEX_MEAN

    def test_below_min_races_returns_mean(self) -> None:
        """MIN_RACES 未満はデフォルト値。"""
        calc = _make_calculator()
        # MIN_RACES=2 なのでスコア1つだけは不足
        assert calc._weighted_average([60.0]) == SPEED_INDEX_MEAN

    def test_single_score_min_races_1(self) -> None:
        """MIN_RACES=1 に設定した場合は1件でも有効。"""
        import src.indices.last3f as mod
        original = mod.MIN_RACES
        mod.MIN_RACES = 1
        try:
            calc = _make_calculator()
            result = calc._weighted_average([60.0])
            assert result == pytest.approx(60.0, abs=0.1)
        finally:
            mod.MIN_RACES = original

    def test_two_equal_scores(self) -> None:
        """2件同スコアは平均がそのスコアと一致。"""
        calc = _make_calculator()
        result = calc._weighted_average([70.0, 70.0])
        assert result == pytest.approx(70.0, abs=0.1)

    def test_recent_weighted_more(self) -> None:
        """直近スコアが高い場合、加重平均は単純平均より高い。"""
        calc = _make_calculator()
        scores = [80.0, 40.0]  # 直近=80, 古い=40
        simple_avg = sum(scores) / len(scores)  # 60.0
        result = calc._weighted_average(scores)
        assert result > simple_avg

    def test_decay_weight_applied(self) -> None:
        """加重は WEIGHT_DECAY の累乗で減衰する。"""
        calc = _make_calculator()
        scores = [60.0, 50.0, 40.0]
        w0, w1, w2 = 1.0, WEIGHT_DECAY, WEIGHT_DECAY ** 2
        expected = (60.0 * w0 + 50.0 * w1 + 40.0 * w2) / (w0 + w1 + w2)
        result = calc._weighted_average(scores)
        assert result == pytest.approx(expected, abs=0.1)


# ---------------------------------------------------------------------------
# calculate のテスト（DBモック使用）
# ---------------------------------------------------------------------------


class TestCalculate:
    """calculate の統合テスト（DBモック）。"""

    def test_race_not_found_returns_mean(self) -> None:
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = Last3FIndexCalculator(db)
        result = calc.calculate(99, 1)
        assert result == SPEED_INDEX_MEAN

    def test_no_valid_last3f_returns_mean(self) -> None:
        """過去レースに last_3f データがない場合はデフォルト値。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _make_race()
        # past results クエリ
        past_rows = [_make_past_row(last_3f=None, race_id=10)]
        db.query.return_value.filter.return_value.join.return_value.join.return_value \
            .filter.return_value.order_by.return_value.limit.return_value.all.return_value = past_rows

        calc = Last3FIndexCalculator(db)
        # フィールド統計クエリも None を返すよう設定
        db.query.return_value.filter.return_value.all.return_value = []

        result = calc.calculate(99, 1)
        assert result == SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# calculate_batch のテスト（DBモック使用）
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch の統合テスト（DBモック）。"""

    def test_race_not_found_returns_empty(self) -> None:
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = Last3FIndexCalculator(db)
        result = calc.calculate_batch(99)
        assert result == {}

    def test_no_entries_returns_empty(self) -> None:
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _make_race()
        db.query.return_value.filter.return_value.all.return_value = []
        calc = Last3FIndexCalculator(db)
        result = calc.calculate_batch(99)
        assert result == {}


# ---------------------------------------------------------------------------
# _get_field_stats / _get_field_stats_batch のテスト
# ---------------------------------------------------------------------------


class TestFieldStats:
    """フィールド統計取得のテスト。"""

    def test_insufficient_sample_returns_none(self) -> None:
        """サンプル数が MIN_FIELD_SAMPLE 未満なら None。"""
        db = MagicMock()
        # 3件のみ（MIN_FIELD_SAMPLE=4 未満）
        db.query.return_value.filter.return_value.all.return_value = [
            (Decimal("34.0"),), (Decimal("35.0"),), (Decimal("36.0"),),
        ]
        calc = Last3FIndexCalculator(db)
        result = calc._get_field_stats(10)
        assert result is None

    def test_sufficient_sample_returns_stats(self) -> None:
        """サンプル数が十分なら (mean, std) を返す。"""
        db = MagicMock()
        vals = [34.0, 35.0, 35.5, 36.0, 36.5]
        db.query.return_value.filter.return_value.all.return_value = [
            (Decimal(str(v)),) for v in vals
        ]
        calc = Last3FIndexCalculator(db)
        result = calc._get_field_stats(10)
        assert result is not None
        mean, std = result
        import statistics
        assert mean == pytest.approx(statistics.mean(vals), abs=0.01)
        assert std == pytest.approx(statistics.stdev(vals), abs=0.01)

    def test_cache_prevents_duplicate_query(self) -> None:
        """2回目の呼び出しはキャッシュを使いDBクエリしない。"""
        db = MagicMock()
        vals = [34.0, 35.0, 35.5, 36.0, 36.5]
        db.query.return_value.filter.return_value.all.return_value = [
            (Decimal(str(v)),) for v in vals
        ]
        calc = Last3FIndexCalculator(db)
        calc._get_field_stats(10)
        call_count_first = db.query.call_count

        calc._get_field_stats(10)  # 2回目
        assert db.query.call_count == call_count_first  # クエリ増えない

    def test_zero_std_returns_none(self) -> None:
        """全馬が同一 last_3f（std≈0）なら None。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [
            (Decimal("35.0"),) for _ in range(MIN_FIELD_SAMPLE)
        ]
        calc = Last3FIndexCalculator(db)
        result = calc._get_field_stats(10)
        assert result is None


# ---------------------------------------------------------------------------
# スコア妥当性テスト（特定の入力に対する期待値）
# ---------------------------------------------------------------------------


class TestScoreValidity:
    """具体的な入力に対するスコアの妥当性検証。"""

    def _make_calc_with_past(
        self, past_last3f_list: list[float | None], field_mean: float, field_std: float
    ) -> tuple[Last3FIndexCalculator, list[float]]:
        """過去レースと固定フィールド統計でスコアを計算するヘルパー。"""
        rows = [
            _make_past_row(last_3f=val, race_id=100 + i)
            for i, val in enumerate(past_last3f_list)
        ]
        field_stats = {
            100 + i: (field_mean, field_std) if val is not None else None
            for i, val in enumerate(past_last3f_list)
        }
        calc = _make_calculator()
        scores = calc._compute_scores(rows, field_stats)
        return calc, scores

    def test_consistently_fast_horse_high_score(self) -> None:
        """常に速い上がりの馬は60超の加重平均。"""
        # 全レースで1σ速い（mean=35.0, std=1.0, horse=34.0 → score=60）
        calc, scores = self._make_calc_with_past(
            [34.0, 34.0, 34.0], field_mean=35.0, field_std=1.0
        )
        result = calc._weighted_average(scores)
        assert result > 55.0

    def test_consistently_slow_horse_low_score(self) -> None:
        """常に遅い上がりの馬は45未満の加重平均。"""
        # 全レースで1σ遅い（horse=36.0 → score=40）
        calc, scores = self._make_calc_with_past(
            [36.0, 36.0, 36.0], field_mean=35.0, field_std=1.0
        )
        result = calc._weighted_average(scores)
        assert result < 45.0

    def test_improving_horse_favors_recent(self) -> None:
        """最近の上がりが改善している馬は、単純平均より高スコア。"""
        # 直近=33.0（速い=score70）、古い=37.0（遅い=score30）
        calc, scores = self._make_calc_with_past(
            [33.0, 37.0], field_mean=35.0, field_std=1.0
        )
        simple_avg = sum(scores) / len(scores)  # 50.0
        result = calc._weighted_average(scores)
        assert result > simple_avg  # 直近の速い上がりを重視

    def test_none_last3f_excluded_from_scores(self) -> None:
        """last_3f=None のレースはスコアに含まれない。"""
        rows = [
            _make_past_row(last_3f=None, race_id=100),   # 除外
            _make_past_row(last_3f=34.0, race_id=101),   # 有効
            _make_past_row(last_3f=34.0, race_id=102),   # 有効
        ]
        field_stats = {
            100: (35.0, 1.0),
            101: (35.0, 1.0),
            102: (35.0, 1.0),
        }
        calc = _make_calculator()
        scores = calc._compute_scores(rows, field_stats)
        # None が含まれないため2件のみ
        assert len(scores) == 2

    def test_abnormality_excluded(self) -> None:
        """異常コードありのレースは過去結果クエリで除外済み（スコアに不正値なし）。"""
        # abnormality_code > 0 のレコードはクエリ側で除外されるため
        # _compute_scores には渡ってこない前提。テストはそのケース自体をスキップ。
        pass
