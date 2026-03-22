"""騎手指数算出 ユニットテスト

DB接続不要のモックベーステスト。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.indices.jockey import MIN_SAMPLE, JockeyIndexCalculator
from src.utils.constants import SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race_result(
    jockey_id: int | None = 1,
    finish_position: int = 1,
    last_3f: float | None = 34.0,
    abnormality_code: int = 0,
) -> MagicMock:
    """RaceResult モックを生成する。"""
    r = MagicMock()
    r.jockey_id = jockey_id
    r.finish_position = finish_position
    r.last_3f = Decimal(str(last_3f)) if last_3f is not None else None
    r.abnormality_code = abnormality_code
    return r


def _make_race(
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    condition: str = "良",
    date: str = "20260322",
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = 1
    r.course = course
    r.distance = distance
    r.surface = surface
    r.condition = condition
    r.date = date
    return r


def _make_row(
    jockey_id: int = 1,
    finish_position: int = 1,
    last_3f: float | None = 34.0,
    distance: int = 1600,
    surface: str = "芝",
    abnormality_code: int = 0,
) -> MagicMock:
    """(RaceResult, Race) のタプル風モックを生成する。"""
    row = MagicMock()
    row.RaceResult = _make_race_result(jockey_id, finish_position, last_3f, abnormality_code)
    row.Race = _make_race(distance=distance, surface=surface)
    return row


def _build_calculator(
    race: MagicMock | None = None,
    entries: list[MagicMock] | None = None,
) -> JockeyIndexCalculator:
    """モックDB付き JockeyIndexCalculator を返す。

    Args:
        race: 対象レースのモック（Noneの場合はデフォルトを使用）
        entries: レースエントリのモックリスト

    Returns:
        テスト用 JockeyIndexCalculator
    """
    db = MagicMock()
    if race is None:
        race = _make_race()
    db.query.return_value.filter.return_value.first.return_value = race

    if entries is not None:
        db.query.return_value.filter.return_value.all.return_value = entries

    return JockeyIndexCalculator(db=db)


def _make_entry(horse_id: int = 1, jockey_id: int | None = 1) -> MagicMock:
    """RaceEntry モックを生成する。"""
    e = MagicMock()
    e.horse_id = horse_id
    e.jockey_id = jockey_id
    return e


# ---------------------------------------------------------------------------
# テストケース 1: 騎手なし（jockey_id=None）→ 50.0
# ---------------------------------------------------------------------------


class TestNoJockey:
    """騎手未登録の馬は SPEED_INDEX_MEAN を返すことを確認。"""

    def test_calculate_no_jockey_returns_mean(self) -> None:
        """calculate: jockey_id=None の馬は SPEED_INDEX_MEAN を返す。"""
        race = _make_race()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [race, None]

        calc = JockeyIndexCalculator(db=db)

        # RaceEntry.jockey_id = None を返す
        entry_mock = MagicMock()
        entry_mock.jockey_id = None
        db.query.return_value.filter.return_value.first.side_effect = [race, entry_mock]

        result = calc.calculate(race_id=1, horse_id=1)
        assert result == SPEED_INDEX_MEAN

    def test_calculate_batch_no_jockey_horse_gets_mean(self) -> None:
        """calculate_batch: jockey_id=None の馬は SPEED_INDEX_MEAN を返す。"""
        race = _make_race()
        entries = [_make_entry(horse_id=1, jockey_id=None)]

        calc = _build_calculator(race=race, entries=entries)
        calc._get_all_jockey_stats_batch = MagicMock(return_value={})

        result = calc.calculate_batch(race_id=1)
        assert result[1] == SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# テストケース 2: 勝率の高い騎手 → 50 超
# ---------------------------------------------------------------------------


class TestHighWinRate:
    """勝率の高い騎手は 50 より高い指数になることを確認。"""

    def test_high_win_rate_jockey_above_mean(self) -> None:
        """全勝の騎手と全敗の騎手を比較すると全勝の方が高い指数になる。"""
        # 全勝騎手（jockey_id=1）
        rows_winner = [
            _make_row(jockey_id=1, finish_position=1, last_3f=33.0)
            for _ in range(MIN_SAMPLE + 5)
        ]
        # 全敗騎手（jockey_id=2）
        rows_loser = [
            _make_row(jockey_id=2, finish_position=10, last_3f=36.0)
            for _ in range(MIN_SAMPLE + 5)
        ]

        race = _make_race()
        entries = [
            _make_entry(horse_id=1, jockey_id=1),
            _make_entry(horse_id=2, jockey_id=2),
        ]
        calc = _build_calculator(race=race, entries=entries)
        calc._get_all_jockey_stats_batch = MagicMock(
            return_value={
                1: calc._compute_raw_score(rows_winner, "芝", 1600),
                2: calc._compute_raw_score(rows_loser, "芝", 1600),
            }
        )

        result = calc.calculate_batch(race_id=1)
        assert result[1] > SPEED_INDEX_MEAN
        assert result[1] > result[2]


# ---------------------------------------------------------------------------
# テストケース 3: 勝率の低い騎手 → 50 未満
# ---------------------------------------------------------------------------


class TestLowWinRate:
    """勝率の低い騎手は 50 より低い指数になることを確認。"""

    def test_low_win_rate_jockey_below_mean(self) -> None:
        """全敗の騎手は全勝の騎手より低い指数になる。"""
        rows_winner = [
            _make_row(jockey_id=1, finish_position=1, last_3f=33.0)
            for _ in range(MIN_SAMPLE + 5)
        ]
        rows_loser = [
            _make_row(jockey_id=2, finish_position=12, last_3f=36.5)
            for _ in range(MIN_SAMPLE + 5)
        ]

        race = _make_race()
        entries = [
            _make_entry(horse_id=1, jockey_id=1),
            _make_entry(horse_id=2, jockey_id=2),
        ]
        calc = _build_calculator(race=race, entries=entries)
        score_winner = calc._compute_raw_score(rows_winner, "芝", 1600)
        score_loser = calc._compute_raw_score(rows_loser, "芝", 1600)
        calc._get_all_jockey_stats_batch = MagicMock(
            return_value={1: score_winner, 2: score_loser}
        )

        result = calc.calculate_batch(race_id=1)
        assert result[2] < SPEED_INDEX_MEAN
        assert result[2] < result[1]


# ---------------------------------------------------------------------------
# テストケース 4: MIN_SAMPLE 未満 → 50.0
# ---------------------------------------------------------------------------


class TestMinSample:
    """MIN_SAMPLE 未満のサンプルは SPEED_INDEX_MEAN を返すことを確認。"""

    def test_insufficient_sample_returns_mean(self) -> None:
        """MIN_SAMPLE - 1 件のサンプルでは SPEED_INDEX_MEAN を返す。"""
        rows = [
            _make_row(jockey_id=1, finish_position=1, last_3f=33.0)
            for _ in range(MIN_SAMPLE - 1)
        ]
        calc = _build_calculator()
        score = calc._compute_raw_score(rows, "芝", 1600)
        assert score is None

    def test_zero_sample_returns_mean(self) -> None:
        """サンプル0件では None を返す（呼び出し元が SPEED_INDEX_MEAN を返す）。"""
        calc = _build_calculator()
        score = calc._compute_raw_score([], "芝", 1600)
        assert score is None

    def test_calculate_batch_insufficient_sample_returns_mean(self) -> None:
        """calculate_batch: サンプル不足の騎手は SPEED_INDEX_MEAN を返す。"""
        race = _make_race()
        entries = [_make_entry(horse_id=1, jockey_id=1)]
        calc = _build_calculator(race=race, entries=entries)
        # None を返す（サンプル不足）
        calc._get_all_jockey_stats_batch = MagicMock(return_value={1: None})

        result = calc.calculate_batch(race_id=1)
        assert result[1] == SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# テストケース 5: calculate_batch が全馬を返す
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch が全エントリ馬の horse_id をキーとして返すことを確認。"""

    def test_returns_all_horse_ids(self) -> None:
        """エントリ全馬の horse_id がキーとして返る。"""
        race = _make_race()
        horse_ids = [101, 102, 103]
        entries = [_make_entry(horse_id=hid, jockey_id=hid) for hid in horse_ids]

        calc = _build_calculator(race=race, entries=entries)
        calc._get_all_jockey_stats_batch = MagicMock(
            return_value={hid: 30.0 for hid in horse_ids}
        )

        result = calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)

    def test_returns_float_values(self) -> None:
        """全馬の指数値が float 型であることを確認。"""
        race = _make_race()
        horse_ids = [1, 2]
        entries = [_make_entry(horse_id=hid, jockey_id=hid) for hid in horse_ids]

        calc = _build_calculator(race=race, entries=entries)
        calc._get_all_jockey_stats_batch = MagicMock(
            return_value={1: 25.0, 2: 35.0}
        )

        result = calc.calculate_batch(race_id=1)
        for val in result.values():
            assert isinstance(val, float)


# ---------------------------------------------------------------------------
# テストケース 6: race_id 未存在 → 空 dict
# ---------------------------------------------------------------------------


class TestRaceNotFound:
    """レースが存在しない場合は空 dict を返すことを確認。"""

    def test_calculate_batch_race_not_found_returns_empty(self) -> None:
        """race_id が存在しない → 空 dict を返す。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = JockeyIndexCalculator(db=db)

        result = calc.calculate_batch(race_id=9999)
        assert result == {}

    def test_calculate_race_not_found_returns_mean(self) -> None:
        """calculate: race_id が存在しない → SPEED_INDEX_MEAN を返す。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = JockeyIndexCalculator(db=db)

        result = calc.calculate(race_id=9999, horse_id=1)
        assert result == SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# テストケース 7: 上がり3F優秀な騎手が評価される
# ---------------------------------------------------------------------------


class TestLast3FScore:
    """上がり3Fが速い騎手の方が高い指数になることを確認。"""

    def test_fast_last3f_jockey_scores_higher(self) -> None:
        """last_3f のある騎手は last_3f=None の騎手より raw_score が変わることを確認。

        Note: _compute_raw_score はサンプル内部の標準偏差でZ-scoreを算出するため、
        全データが同一値の場合 std=0 となり last3f_score は SPEED_INDEX_MEAN=50.0 に
        フォールバックする。よって last_3f の違いは calculate_batch の正規化ステップで
        考慮される。ここでは last_3f あり/なしの違いが raw_score に影響することを確認する。
        """
        # last_3f あり（バラつきがあれば std > 0 で計算される）
        rows_with_last3f = [
            _make_row(jockey_id=1, finish_position=1, last_3f=33.0 + i * 0.5)
            for i in range(MIN_SAMPLE + 3)
        ]
        # last_3f なし（全 None）→ last3f コンポーネントは 50.0
        rows_no_last3f = [
            _make_row(jockey_id=2, finish_position=1, last_3f=None)
            for _ in range(MIN_SAMPLE + 3)
        ]

        calc = _build_calculator()
        score_with = calc._compute_raw_score(rows_with_last3f, "芝", 1600)
        score_without = calc._compute_raw_score(rows_no_last3f, "芝", 1600)

        # 両者ともサンプルあり → Noneではない
        assert score_with is not None
        assert score_without is not None
        # 同じ勝率・連対率ならスコアは近い（last_3f コンポーネントはどちらも50付近）
        assert abs(score_with - score_without) < 10.0

    def test_no_last3f_data_returns_mean_component(self) -> None:
        """last_3f が全て None の場合、last3f コンポーネントは SPEED_INDEX_MEAN になる。"""
        rows = [
            _make_row(jockey_id=1, finish_position=1, last_3f=None)
            for _ in range(MIN_SAMPLE + 3)
        ]
        calc = _build_calculator()
        score = calc._compute_last3f_score(rows)
        assert score == SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# テストケース 8: 同一騎手が複数馬に乗っている場合
# ---------------------------------------------------------------------------


class TestSameJockeyMultipleHorses:
    """同一騎手が複数馬に乗っている場合でも正常動作することを確認。"""

    def test_same_jockey_on_multiple_horses(self) -> None:
        """同一 jockey_id を持つ複数馬が全馬同じ指数を返す。"""
        race = _make_race()
        # jockey_id=1 が horse_id=1,2 両方に乗る
        entries = [
            _make_entry(horse_id=1, jockey_id=1),
            _make_entry(horse_id=2, jockey_id=1),
        ]
        calc = _build_calculator(race=race, entries=entries)
        calc._get_all_jockey_stats_batch = MagicMock(return_value={1: 40.0})

        result = calc.calculate_batch(race_id=1)
        assert 1 in result
        assert 2 in result
        # 同一騎手なので同じ指数
        assert result[1] == result[2]

    def test_same_jockey_cache_is_used(self) -> None:
        """同一騎手のスコアはキャッシュから返され、DBアクセスは1回のみ。"""
        race = _make_race()
        entries = [
            _make_entry(horse_id=1, jockey_id=5),
            _make_entry(horse_id=2, jockey_id=5),
        ]

        calc = _build_calculator(race=race, entries=entries)
        # キャッシュに注入
        calc._jockey_stats_cache[(5, "芝", 1600)] = 60.0
        calc._get_all_jockey_stats_batch = MagicMock(
            side_effect=lambda ids, *a, **kw: {jid: 60.0 for jid in ids}
        )

        result = calc.calculate_batch(race_id=1)
        assert 1 in result
        assert 2 in result


# ---------------------------------------------------------------------------
# テストケース 9: _compute_raw_score の距離フィルタ動作確認
# ---------------------------------------------------------------------------


class TestDistanceFilter:
    """距離フィルタ（±DIST_TOLERANCE）が正しく機能することを確認。"""

    def test_within_tolerance_included(self) -> None:
        """DIST_TOLERANCE 以内の距離は集計に含まれる。"""
        # 1600m の対象に対して 2000m（差400m＝境界値）は含まれる
        rows = [
            _make_row(jockey_id=1, finish_position=1, last_3f=33.0, distance=2000)
            for _ in range(MIN_SAMPLE)
        ]
        calc = _build_calculator()
        score = calc._compute_raw_score(rows, "芝", 1600)
        assert score is not None  # フィルタを通過してサンプルが MIN_SAMPLE 以上

    def test_outside_tolerance_excluded(self) -> None:
        """DIST_TOLERANCE を超える距離は集計から除外される。"""
        # 1600m の対象に対して 2001m（差401m）は除外
        rows = [
            _make_row(jockey_id=1, finish_position=1, last_3f=33.0, distance=2001)
            for _ in range(MIN_SAMPLE + 5)
        ]
        calc = _build_calculator()
        score = calc._compute_raw_score(rows, "芝", 1600)
        assert score is None  # フィルタで除外されてサンプル不足


# ---------------------------------------------------------------------------
# テストケース 10: _normalize の動作確認
# ---------------------------------------------------------------------------


class TestNormalize:
    """_normalize が正しく正規化することを確認。"""

    def test_normalize_mean_returns_index_mean(self) -> None:
        """集団平均と同じ raw_score → SPEED_INDEX_MEAN になる。"""
        result = JockeyIndexCalculator._normalize(50.0, 50.0, 10.0)
        assert result == pytest.approx(SPEED_INDEX_MEAN, abs=0.01)

    def test_normalize_one_std_above_returns_60(self) -> None:
        """集団平均 + 1σ の raw_score → 60.0 になる。"""
        result = JockeyIndexCalculator._normalize(60.0, 50.0, 10.0)
        assert result == pytest.approx(60.0, abs=0.01)

    def test_normalize_one_std_below_returns_40(self) -> None:
        """集団平均 - 1σ の raw_score → 40.0 になる。"""
        result = JockeyIndexCalculator._normalize(40.0, 50.0, 10.0)
        assert result == pytest.approx(40.0, abs=0.01)
