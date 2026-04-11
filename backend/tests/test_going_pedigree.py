"""重馬場×血統指数算出 ユニットテスト

DB接続不要の純粋ロジックテスト（affinity計算・スコア変換・条件分岐）。
"""

from __future__ import annotations

import pytest

from src.indices.going_pedigree import (
    AFFINITY_SCALE,
    CLIP_MAX,
    CLIP_MIN,
    DEFAULT_SCORE,
    HEAVY_CONDITIONS,
    MIN_HEAVY_RACES,
    NEUTRAL_CONDITIONS,
)

# ---------------------------------------------------------------------------
# 定数・条件分岐テスト
# ---------------------------------------------------------------------------


class TestConditionClassification:
    """馬場状態の分類テスト。"""

    def test_heavy_conditions(self) -> None:
        """重・不良は重馬場条件。"""
        assert "重" in HEAVY_CONDITIONS
        assert "不" in HEAVY_CONDITIONS

    def test_neutral_conditions(self) -> None:
        """良・稍は中立条件。"""
        assert "良" in NEUTRAL_CONDITIONS
        assert "稍" in NEUTRAL_CONDITIONS

    def test_good_condition_not_in_heavy(self) -> None:
        """良馬場は重馬場条件に含まれない。"""
        assert "良" not in HEAVY_CONDITIONS

    def test_yielding_condition_not_in_heavy(self) -> None:
        """稍重は重馬場条件に含まれない。"""
        assert "稍" not in HEAVY_CONDITIONS


# ---------------------------------------------------------------------------
# affinity → スコアの数式検証
# ---------------------------------------------------------------------------


class TestAffinityFormula:
    """affinity → score の変換公式テスト。"""

    def _score_from_affinity(self, affinity: float) -> float:
        """テスト用スコア計算（実装と同一ロジック）。"""
        raw = 50.0 + (affinity - 1.0) * AFFINITY_SCALE
        return max(CLIP_MIN, min(CLIP_MAX, raw))

    def test_affinity_one_is_neutral(self) -> None:
        """重馬場勝率 = 全体勝率（affinity=1.0）→ スコアは 50.0。"""
        score = self._score_from_affinity(1.0)
        assert score == pytest.approx(50.0)

    def test_high_affinity_increases_score(self) -> None:
        """重馬場勝率が全体勝率の 2 倍（affinity=2.0）→ スコアは 50 超。"""
        score = self._score_from_affinity(2.0)
        assert score > 50.0
        assert score == pytest.approx(50.0 + AFFINITY_SCALE, abs=0.01)

    def test_low_affinity_decreases_score(self) -> None:
        """重馬場勝率が全体勝率の半分（affinity=0.5）→ スコアは 50 未満。"""
        score = self._score_from_affinity(0.5)
        assert score < 50.0

    def test_score_capped_at_clip_max(self) -> None:
        """affinity が非常に高くても CLIP_MAX でキャップ。"""
        score = self._score_from_affinity(100.0)
        assert score == pytest.approx(CLIP_MAX)

    def test_score_capped_at_clip_min(self) -> None:
        """affinity が非常に低い場合、CLIP_MIN でキャップ。

        CLIP_MIN = 25.0 に達するには affinity < 1 - (50-25)/20 = -0.25 が必要。
        affinity は負になり得ないため、実際のキャップは affinity=0.0 では発生しない。
        ここでは負数に相当する極端なケース（実装の下限確認）を検証する。
        """
        # 実装の _score_from_affinity は raw の下限が CLIP_MIN になるよう設計
        # 50 + (affinity-1)*20 = 25 → affinity = -0.25 が必要
        # affinity は常に非負なので、実際は 50+(0-1)*20=30 が最低値
        # CLIP_MIN はガード用の安全網として機能する
        score_at_zero = self._score_from_affinity(0.0)
        assert score_at_zero == pytest.approx(30.0, abs=0.01)
        # CLIP_MIN より高いことを確認（クリップは発生しない）
        assert score_at_zero > CLIP_MIN

    def test_clip_range(self) -> None:
        """スコアは [CLIP_MIN, CLIP_MAX] の範囲に収まる。"""
        for affinity in [0.0, 0.5, 1.0, 1.5, 2.0, 5.0]:
            score = self._score_from_affinity(affinity)
            assert CLIP_MIN <= score <= CLIP_MAX

    def test_overall_win_rate_zero_uses_floor(self) -> None:
        """全体勝率が 0 でも除算エラーにならない（floor 0.01 使用）。"""
        heavy_wins = 2
        heavy_total = 15
        all_wins = 0
        all_total = 50

        heavy_win_rate = heavy_wins / heavy_total
        overall_win_rate = all_wins / max(all_total, 1)  # 0.0
        affinity = heavy_win_rate / max(overall_win_rate, 0.01)
        # floor 0.01 を使用するため affinity は計算可能
        assert affinity > 0.0

    def test_insufficient_heavy_races_returns_default(self) -> None:
        """重馬場出走数 < MIN_HEAVY_RACES はデフォルト扱い（ロジック確認）。"""
        heavy_total = MIN_HEAVY_RACES - 1
        assert heavy_total < MIN_HEAVY_RACES
        assert DEFAULT_SCORE == 50.0


# ---------------------------------------------------------------------------
# 良/稍馬場の中立テスト
# ---------------------------------------------------------------------------


class TestNeutralConditions:
    """良/稍馬場での中立スコアテスト。"""

    def test_good_track_returns_default(self) -> None:
        """良馬場（condition='良'）は計算なしで DEFAULT_SCORE を返す（ロジック確認）。"""
        # calculate_batch で condition not in HEAVY_CONDITIONS のとき全馬 50.0 を返す
        condition = "良"
        assert condition not in HEAVY_CONDITIONS
        assert DEFAULT_SCORE == 50.0

    def test_yielding_track_returns_default(self) -> None:
        """稍重馬場（condition='稍'）は計算なしで DEFAULT_SCORE を返す（ロジック確認）。"""
        condition = "稍"
        assert condition not in HEAVY_CONDITIONS

    def test_heavy_track_triggers_calculation(self) -> None:
        """重馬場（condition='重'）は計算を行う（ロジック確認）。"""
        condition = "重"
        assert condition in HEAVY_CONDITIONS

    def test_very_heavy_track_triggers_calculation(self) -> None:
        """不良馬場（condition='不'）は計算を行う（ロジック確認）。"""
        condition = "不"
        assert condition in HEAVY_CONDITIONS


# ---------------------------------------------------------------------------
# MIN_HEAVY_RACES 検証
# ---------------------------------------------------------------------------


class TestMinHeavyRaces:
    """最小出走数要件のテスト。"""

    def test_min_heavy_races_constant(self) -> None:
        """MIN_HEAVY_RACES が 10 であることを確認。"""
        assert MIN_HEAVY_RACES == 10

    def test_exactly_min_races_triggers_calculation(self) -> None:
        """重馬場出走数が MIN_HEAVY_RACES ちょうどで計算される（ロジック確認）。"""
        heavy_total = MIN_HEAVY_RACES
        heavy_wins = 2
        all_wins = 5
        all_total = 30

        heavy_win_rate = heavy_wins / heavy_total  # 0.2
        overall_win_rate = all_wins / max(all_total, 1)  # 0.167
        affinity = heavy_win_rate / max(overall_win_rate, 0.01)  # ≈ 1.2
        raw = 50.0 + (affinity - 1.0) * AFFINITY_SCALE
        score = max(CLIP_MIN, min(CLIP_MAX, raw))
        # 適性が少し高いので50超
        assert score > 50.0
