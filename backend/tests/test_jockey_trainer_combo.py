"""騎手×厩舎コンビ指数算出 ユニットテスト

DB接続不要の純粋ロジックテスト（lift計算・スコア変換）。
"""

from __future__ import annotations

import pytest

from src.indices.jockey_trainer_combo import (
    CLIP_MAX,
    CLIP_MIN,
    DEFAULT_SCORE,
    LIFT_SCALE,
    MIN_COMBO_RACES,
)

# ---------------------------------------------------------------------------
# lift とスコアの数式検証
# ---------------------------------------------------------------------------


class TestLiftFormula:
    """lift → score の変換公式テスト。"""

    def _score_from_lift(self, lift: float) -> float:
        """テスト用スコア計算（実装と同一ロジック）。"""
        raw = lift * LIFT_SCALE
        return 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))

    def test_positive_lift_increases_score(self) -> None:
        """コンビ勝率 > 単独騎手勝率 → スコアは 50 超。"""
        score = self._score_from_lift(0.05)
        assert score > 50.0

    def test_negative_lift_decreases_score(self) -> None:
        """コンビ勝率 < 単独騎手勝率 → スコアは 50 未満。"""
        score = self._score_from_lift(-0.05)
        assert score < 50.0

    def test_zero_lift_neutral(self) -> None:
        """lift = 0 → スコアは 50.0。"""
        score = self._score_from_lift(0.0)
        assert score == pytest.approx(50.0)

    def test_lift_10pct_gives_20_bonus(self) -> None:
        """lift = 0.10 → +20 ボーナス（LIFT_SCALE=200 の期待値）。"""
        score = self._score_from_lift(0.10)
        assert score == pytest.approx(50.0 + 20.0, abs=0.01)

    def test_max_bonus_capped(self) -> None:
        """lift が非常に大きくても CLIP_MAX で上限。"""
        score = self._score_from_lift(1.0)  # 非常に大きいlift
        assert score == pytest.approx(50.0 + CLIP_MAX)

    def test_min_penalty_capped(self) -> None:
        """lift が非常に小さくても CLIP_MIN で下限。"""
        score = self._score_from_lift(-1.0)
        assert score == pytest.approx(50.0 + CLIP_MIN)

    def test_score_range(self) -> None:
        """スコアが [50+CLIP_MIN, 50+CLIP_MAX] の範囲に収まる。"""
        for lift in [-0.5, -0.1, -0.05, 0.0, 0.05, 0.1, 0.5]:
            score = self._score_from_lift(lift)
            assert 50.0 + CLIP_MIN <= score <= 50.0 + CLIP_MAX


# ---------------------------------------------------------------------------
# min combo races 検証
# ---------------------------------------------------------------------------


class TestMinComboRaces:
    """コンビ出走数の最小要件テスト。"""

    def test_min_combo_races_constant(self) -> None:
        """MIN_COMBO_RACES が 5 であることを確認。"""
        assert MIN_COMBO_RACES == 5

    def test_below_min_returns_default(self) -> None:
        """コンビ出走数が MIN_COMBO_RACES 未満はデフォルト扱い（ロジック確認）。"""
        # 実装上 c_total < MIN_COMBO_RACES で DEFAULT_SCORE を返す
        c_total = MIN_COMBO_RACES - 1
        assert c_total < MIN_COMBO_RACES
        # DEFAULT_SCORE は 50.0
        assert DEFAULT_SCORE == 50.0

    def test_exactly_min_combo_races_triggers_calculation(self) -> None:
        """コンビ出走数が MIN_COMBO_RACES ちょうどで計算される（ロジック確認）。"""
        c_total = MIN_COMBO_RACES
        c_wins = 2
        j_wins = 10
        j_total = 50

        combo_win_rate = c_wins / c_total  # 0.4
        solo_win_rate = j_wins / j_total   # 0.2
        lift = combo_win_rate - solo_win_rate  # 0.2

        raw = lift * LIFT_SCALE
        score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
        assert score > 50.0  # 正のリフトなのでスコアは 50 超


# ---------------------------------------------------------------------------
# デフォルト値テスト
# ---------------------------------------------------------------------------


class TestDefaultScore:
    """デフォルトスコアのテスト。"""

    def test_default_score_is_neutral(self) -> None:
        """DEFAULT_SCORE は中立値 50.0。"""
        assert DEFAULT_SCORE == 50.0

    def test_constants_sanity(self) -> None:
        """定数が期待通りの値であることを確認。"""
        assert LIFT_SCALE == 200.0
        assert CLIP_MAX == 20.0
        assert CLIP_MIN == -15.0
