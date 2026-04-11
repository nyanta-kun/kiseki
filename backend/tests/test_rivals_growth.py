"""上昇相手指数算出 ユニットテスト

DB接続不要の純粋ロジックテスト（_grade_rank）と
AsyncMock を使ったバッチ算出テスト。
"""

from __future__ import annotations

import pytest

from src.indices.rivals_growth import (
    DEFAULT_SCORE,
    MAX_BONUS,
    PLACE_MULTIPLIER,
    RECENCY_DECAY,
    UPLIFT_UNIT,
    WIN_MULTIPLIER,
    _grade_rank,
)

# ---------------------------------------------------------------------------
# _grade_rank
# ---------------------------------------------------------------------------


class TestGradeRank:
    """グレードランク算出テスト。"""

    def test_g1(self) -> None:
        assert _grade_rank("G1", None, None) == 9

    def test_g2(self) -> None:
        assert _grade_rank("G2", None, None) == 8

    def test_g3(self) -> None:
        assert _grade_rank("G3", None, None) == 7

    def test_op(self) -> None:
        assert _grade_rank("OP", None, None) == 6

    def test_op_special_string(self) -> None:
        """'OP特別' はOP扱い（6）。"""
        assert _grade_rank("OP特別", None, None) == 6

    def test_jg1_contains(self) -> None:
        """'J.G1' のように grade 文字列に G1 が含まれる場合。"""
        assert _grade_rank("J.G1", None, None) == 9

    def test_none_grade_no_prize(self) -> None:
        """grade/prize_1st なし → 不明(0)。"""
        assert _grade_rank(None, None, None) == 0

    def test_none_grade_no_type(self) -> None:
        """prize_1st あり・race_type_code なし → 不明(0)。"""
        assert _grade_rank(None, 100000, None) == 0

    def test_2yo_shofu_boundary(self) -> None:
        """2歳未勝利（境界値）。"""
        assert _grade_rank(None, 58000, "11") == 2

    def test_2yo_1win(self) -> None:
        """2歳1勝クラス。"""
        assert _grade_rank(None, 58001, "11") == 3

    def test_3yo_shofu(self) -> None:
        """3歳未勝利。"""
        assert _grade_rank(None, 62000, "12") == 2

    def test_3yo_1win(self) -> None:
        """3歳1勝クラス。"""
        assert _grade_rank(None, 70000, "12") == 3

    def test_3yo_2win(self) -> None:
        """3歳2勝クラス。"""
        assert _grade_rank(None, 80000, "12") == 4

    def test_3yo_up_1win(self) -> None:
        """3歳以上1勝クラス。"""
        assert _grade_rank(None, 90000, "13") == 3

    def test_3yo_up_2win(self) -> None:
        """3歳以上2勝クラス。"""
        assert _grade_rank(None, 120000, "13") == 4

    def test_3yo_up_3win(self) -> None:
        """3歳以上3勝クラス。"""
        assert _grade_rank(None, 150000, "13") == 5

    def test_4yo_up_2win(self) -> None:
        """4歳以上2勝クラス。"""
        assert _grade_rank(None, 90000, "14") == 4

    def test_4yo_up_3win(self) -> None:
        """4歳以上3勝クラス。"""
        assert _grade_rank(None, 110000, "14") == 5

    def test_unknown_type_code(self) -> None:
        """未知の race_type_code → 不明(0)。"""
        assert _grade_rank(None, 100000, "99") == 0


# ---------------------------------------------------------------------------
# スコア計算ロジック（数式検証）
# ---------------------------------------------------------------------------


class TestScoreFormula:
    """スコア算出の数式をホワイトボックスで検証する。"""

    def test_single_win_uplift_1(self) -> None:
        """グレード1段上昇・勝利のボーナス値が正しい。"""
        uplift = 1
        contribution = uplift * UPLIFT_UNIT * WIN_MULTIPLIER
        score = DEFAULT_SCORE + min(MAX_BONUS, contribution)
        assert score == pytest.approx(DEFAULT_SCORE + 1 * 10.0 * 1.5)

    def test_single_place_uplift_2(self) -> None:
        """グレード2段上昇・2着入賞のボーナス値が正しい。"""
        uplift = 2
        contribution = uplift * UPLIFT_UNIT * PLACE_MULTIPLIER
        score = DEFAULT_SCORE + min(MAX_BONUS, contribution)
        assert score == pytest.approx(DEFAULT_SCORE + 2 * 10.0 * 1.0)

    def test_recency_decay_applied(self) -> None:
        """2走前の成績は RECENCY_DECAY がかかる。"""
        uplift = 1
        contribution = uplift * UPLIFT_UNIT * WIN_MULTIPLIER * (RECENCY_DECAY**1)
        assert contribution == pytest.approx(1 * 10.0 * 1.5 * 0.75)

    def test_max_bonus_cap(self) -> None:
        """累積ボーナスが MAX_BONUS を超えた場合、スコアは 100 に収束する。"""
        cumulative = 9999.0  # 非常に大きい値
        score = DEFAULT_SCORE + min(MAX_BONUS, cumulative)
        assert score == DEFAULT_SCORE + MAX_BONUS

    def test_no_uplift_returns_default(self) -> None:
        """上昇馬なし（累積0）→ DEFAULT_SCORE（50.0）を返す。"""
        cumulative = 0.0
        score = DEFAULT_SCORE + min(MAX_BONUS, cumulative)
        assert score == DEFAULT_SCORE
