"""成長曲線指数算出 ユニットテスト

DB接続不要の純粋ロジックテスト（傾き算出・年齢補正・スコア計算）と
AsyncMock を使ったバッチ算出テスト。
"""

from __future__ import annotations

import pytest

from src.indices.career_phase import (
    AGE_BONUS_2YO,
    AGE_BONUS_3YO_SPRING,
    DEFAULT_SCORE,
    LOOKBACK_RACES,
    SLOPE_CLIP_MAX,
    SLOPE_CLIP_MIN,
    SLOPE_SCALE,
    CareerPhaseIndexCalculator,
    _age_adjustment,
    _compute_slope,
)


# ---------------------------------------------------------------------------
# _compute_slope
# ---------------------------------------------------------------------------


class TestComputeSlope:
    """OLS 傾き算出のテスト。"""

    def test_increasing_trend(self) -> None:
        """y が増加する場合、正の傾きを返す。"""
        x = [0.0, 1.0, 2.0, 3.0, 4.0]
        y = [0.1, 0.3, 0.5, 0.7, 0.9]
        slope = _compute_slope(x, y)
        assert slope > 0

    def test_decreasing_trend(self) -> None:
        """y が減少する場合、負の傾きを返す。"""
        x = [0.0, 1.0, 2.0, 3.0, 4.0]
        y = [0.9, 0.7, 0.5, 0.3, 0.1]
        slope = _compute_slope(x, y)
        assert slope < 0

    def test_flat_trend(self) -> None:
        """y が一定の場合、傾き 0 を返す。"""
        x = [0.0, 1.0, 2.0]
        y = [0.5, 0.5, 0.5]
        slope = _compute_slope(x, y)
        assert slope == pytest.approx(0.0)

    def test_single_point_returns_zero(self) -> None:
        """データ点が 1 つの場合、0.0 を返す。"""
        assert _compute_slope([0.0], [0.5]) == 0.0

    def test_empty_returns_zero(self) -> None:
        """データなしの場合、0.0 を返す。"""
        assert _compute_slope([], []) == 0.0

    def test_perfect_linear_slope(self) -> None:
        """完全な線形傾きが正確に計算される。"""
        x = [0.0, 1.0, 2.0, 3.0]
        y = [0.0, 0.2, 0.4, 0.6]
        slope = _compute_slope(x, y)
        assert slope == pytest.approx(0.2, abs=1e-6)


# ---------------------------------------------------------------------------
# _age_adjustment
# ---------------------------------------------------------------------------


class TestAgeAdjustment:
    """馬齢補正のテスト。"""

    def test_2yo_spring_bonus(self) -> None:
        """2歳は月問わず +5 ボーナス。"""
        assert _age_adjustment(2, 4) == pytest.approx(AGE_BONUS_2YO)

    def test_2yo_autumn_bonus(self) -> None:
        """2歳は秋でも +5 ボーナス。"""
        assert _age_adjustment(2, 10) == pytest.approx(AGE_BONUS_2YO)

    def test_3yo_spring_bonus(self) -> None:
        """3歳 春（1-6月）は +5 ボーナス。"""
        for month in [1, 2, 3, 4, 5, 6]:
            assert _age_adjustment(3, month) == pytest.approx(AGE_BONUS_3YO_SPRING)

    def test_3yo_autumn_no_bonus(self) -> None:
        """3歳 秋以降（7-12月）はボーナスなし。"""
        for month in [7, 8, 9, 10, 11, 12]:
            assert _age_adjustment(3, month) == pytest.approx(0.0)

    def test_4yo_no_bonus(self) -> None:
        """4歳はボーナスなし。"""
        assert _age_adjustment(4, 5) == pytest.approx(0.0)

    def test_5yo_no_bonus(self) -> None:
        """5歳以上はボーナスなし。"""
        assert _age_adjustment(5, 3) == pytest.approx(0.0)

    def test_none_age_no_bonus(self) -> None:
        """年齢不明（None）はボーナスなし。"""
        assert _age_adjustment(None, 5) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# CareerPhaseIndexCalculator._compute_score
# ---------------------------------------------------------------------------


class TestComputeScore:
    """スコア算出ロジックのホワイトボックステスト。"""

    def setup_method(self) -> None:
        """テスト用 calculator インスタンスを生成する（DB 不要）。"""
        self.calc = CareerPhaseIndexCalculator.__new__(CareerPhaseIndexCalculator)

    def test_insufficient_data_returns_default(self) -> None:
        """データ点 < 2 の場合、DEFAULT_SCORE を返す。"""
        assert self.calc._compute_score([], None, 5) == DEFAULT_SCORE
        assert self.calc._compute_score([(1, 10)], None, 5) == DEFAULT_SCORE

    def test_improving_trajectory(self) -> None:
        """最近の着順が良い（上昇中）→ スコアは 50 を超える。

        past_data は新しい順: [(1位/10頭), (5位/10頭), (8位/10頭)]
        x=[0,1,2], y=[1.0, 0.556, 0.222] → slope < 0 → improvement_score > 0
        """
        past_data = [(1, 10), (5, 10), (8, 10)]
        score = self.calc._compute_score(past_data, 4, 5)
        assert score > DEFAULT_SCORE

    def test_declining_trajectory(self) -> None:
        """最近の着順が悪い（下降中）→ スコアは 50 未満。

        past_data は新しい順: [(8位/10頭), (5位/10頭), (1位/10頭)]
        x=[0,1,2], y=[0.222, 0.556, 1.0] → slope > 0 → improvement_score < 0
        """
        past_data = [(8, 10), (5, 10), (1, 10)]
        score = self.calc._compute_score(past_data, 4, 5)
        assert score < DEFAULT_SCORE

    def test_flat_trajectory(self) -> None:
        """着順変化なし（中立）→ スコアは 50 付近。"""
        past_data = [(5, 10), (5, 10), (5, 10), (5, 10)]
        score = self.calc._compute_score(past_data, 4, 5)
        assert score == pytest.approx(DEFAULT_SCORE, abs=1.0)

    def test_age_bonus_applied(self) -> None:
        """2歳馬のスコアには年齢ボーナスが加算される。"""
        past_data = [(5, 10), (5, 10), (5, 10)]  # フラット → slope_score=50
        score_2yo = self.calc._compute_score(past_data, 2, 5)
        score_4yo = self.calc._compute_score(past_data, 4, 5)
        assert score_2yo == pytest.approx(score_4yo + AGE_BONUS_2YO, abs=0.5)

    def test_score_capped_at_100(self) -> None:
        """スコアは 100 を超えない。"""
        # 極端な上昇トレンド
        past_data = [(1, 10)] + [(10, 10)] * (LOOKBACK_RACES - 1)
        score = self.calc._compute_score(past_data, 2, 5)  # 2歳ボーナスも加算
        assert score <= 100.0

    def test_score_not_below_zero(self) -> None:
        """スコアは 0 を下回らない。"""
        # 極端な下降トレンド
        past_data = [(10, 10)] + [(1, 10)] * (LOOKBACK_RACES - 1)
        score = self.calc._compute_score(past_data, 4, 5)
        assert score >= 0.0

    def test_two_data_points_used(self) -> None:
        """データ点がちょうど 2 つでも計算される（デフォルト返さず）。"""
        past_data = [(1, 10), (10, 10)]
        score = self.calc._compute_score(past_data, 4, 5)
        # 上昇傾向なのでデフォルトより高いはず
        assert score != DEFAULT_SCORE

    def test_3yo_spring_bonus(self) -> None:
        """3歳 春のスコアには年齢ボーナスが加算される。"""
        past_data = [(5, 10), (5, 10), (5, 10)]
        score_spring = self.calc._compute_score(past_data, 3, 4)  # 4月
        score_autumn = self.calc._compute_score(past_data, 3, 9)  # 9月
        assert score_spring == pytest.approx(score_autumn + AGE_BONUS_3YO_SPRING, abs=0.5)
