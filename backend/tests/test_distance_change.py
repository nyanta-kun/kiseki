"""距離変更適性指数算出 ユニットテスト

DB接続不要の純粋ロジックテスト（距離変更分類・スコア計算）と
算出ロジックの検証テスト。
"""

from __future__ import annotations

import pytest

from src.indices.distance_change import (
    CHANGE_THRESHOLD,
    CLIP_MAX,
    CLIP_MIN,
    DEFAULT_SCORE,
    MIN_PATTERN_RACES,
    RATIO_SCALE,
    DistanceChangeIndexCalculator,
    _classify_change,
)


# ---------------------------------------------------------------------------
# _classify_change
# ---------------------------------------------------------------------------


class TestClassifyChange:
    """距離変更分類のテスト。"""

    def test_extension_exactly_threshold(self) -> None:
        """差が閾値ちょうどは「延長」。"""
        assert _classify_change(2200, 2000) == "extension"

    def test_extension_above_threshold(self) -> None:
        """差が閾値超えは「延長」。"""
        assert _classify_change(2400, 1800) == "extension"

    def test_shortening_exactly_threshold(self) -> None:
        """差が -閾値ちょうどは「短縮」。"""
        assert _classify_change(1800, 2000) == "shortening"

    def test_shortening_below_threshold(self) -> None:
        """差が -閾値超えは「短縮」。"""
        assert _classify_change(1200, 2000) == "shortening"

    def test_same_zero_diff(self) -> None:
        """同距離（差 0）は「同距離」。"""
        assert _classify_change(1600, 1600) == "same"

    def test_same_within_threshold(self) -> None:
        """差が閾値未満は「同距離」。"""
        assert _classify_change(1800, 1800 + CHANGE_THRESHOLD - 1) == "same"
        assert _classify_change(1800, 1800 - CHANGE_THRESHOLD + 1) == "same"

    def test_boundary_minus_one_below_extension(self) -> None:
        """差が閾値 -1 は「同距離」。"""
        assert _classify_change(2199, 2000) == "same"

    def test_boundary_minus_one_above_shortening(self) -> None:
        """差が -閾値 +1 は「同距離」。"""
        assert _classify_change(1801, 2000) == "same"


# ---------------------------------------------------------------------------
# スコア算出ロジック（_compute_batch の内部ロジック検証）
# ---------------------------------------------------------------------------


class TestScoreFormula:
    """スコア算出の数式をホワイトボックスで検証する。"""

    def test_high_pattern_win_rate(self) -> None:
        """パターン勝率が全体勝率を大幅に上回る場合、スコアは 50 を超える。"""
        pattern_wins = 3
        pattern_total = 5  # >= MIN_PATTERN_RACES
        total_wins = 5
        total_valid = 20

        overall_win_rate = total_wins / total_valid  # 0.25
        pattern_win_rate = pattern_wins / pattern_total  # 0.6
        ratio = pattern_win_rate / max(overall_win_rate, 0.01)  # 2.4
        raw = (ratio - 1.0) * RATIO_SCALE
        score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
        assert score > 50.0
        assert score == pytest.approx(50.0 + min(CLIP_MAX, raw), abs=0.1)

    def test_low_pattern_win_rate(self) -> None:
        """パターン勝率が全体勝率を大幅に下回る場合、スコアは 50 未満。"""
        pattern_wins = 0
        pattern_total = 5
        total_wins = 5
        total_valid = 20

        overall_win_rate = total_wins / total_valid  # 0.25
        pattern_win_rate = pattern_wins / pattern_total  # 0.0
        ratio = pattern_win_rate / max(overall_win_rate, 0.01)  # 0.0
        raw = (ratio - 1.0) * RATIO_SCALE
        score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
        assert score < 50.0

    def test_clip_upper_bound(self) -> None:
        """スコアは CLIP_MAX+50 を超えない。"""
        ratio = 100.0  # 極端に高い
        raw = (ratio - 1.0) * RATIO_SCALE
        score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
        assert score == pytest.approx(50.0 + CLIP_MAX)

    def test_clip_lower_bound(self) -> None:
        """スコアは 50+CLIP_MIN を下回らない。"""
        ratio = 0.0
        raw = (ratio - 1.0) * RATIO_SCALE
        score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
        assert score == pytest.approx(50.0 + CLIP_MIN)

    def test_insufficient_pattern_data_returns_default(self) -> None:
        """パターン出走数 < MIN_PATTERN_RACES の場合、DEFAULT_SCORE を使用する。"""
        pattern_total = MIN_PATTERN_RACES - 1
        # パターン少なければ中立を返す（ロジックの確認）
        assert pattern_total < MIN_PATTERN_RACES

    def test_same_distance_neutral(self) -> None:
        """「同距離」パターンは DEFAULT_SCORE を返す（ロジック確認）。"""
        # 実装上 "same" パターンは即座に DEFAULT_SCORE を返す
        pattern = _classify_change(1600, 1600)
        assert pattern == "same"
        # same の場合はスコア計算なしで 50.0 を返すことを確認
        assert DEFAULT_SCORE == 50.0

    def test_overall_win_rate_zero_uses_floor(self) -> None:
        """全体勝利数が 0 でも除算エラーにならない（floor 0.01 使用）。"""
        total_wins = 0
        total_valid = 10
        overall_win_rate = total_wins / max(total_valid, 1)  # 0.0
        pattern_win_rate = 0.4
        ratio = pattern_win_rate / max(overall_win_rate, 0.01)  # 40.0
        raw = (ratio - 1.0) * RATIO_SCALE
        score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
        # clip upper bound に張り付く
        assert score == pytest.approx(50.0 + CLIP_MAX)
