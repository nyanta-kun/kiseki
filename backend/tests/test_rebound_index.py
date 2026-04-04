"""巻き返し指数算出 ユニットテスト

DB接続不要の純粋ロジックテスト（_compute_score / _has_disadvantage）と
AsyncMock を使ったバッチ算出テスト。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.indices.rebound import (
    CHRONIC_SLIPSTART_SCORE,
    DEFAULT_SCORE,
    ReboundIndexCalculator,
    _compute_score,
    _has_disadvantage,
    _has_slipstart,
)


# ---------------------------------------------------------------------------
# _has_disadvantage
# ---------------------------------------------------------------------------


class TestHasDisadvantage:
    """不利キーワード検出テスト。"""

    def test_none_remarks(self) -> None:
        assert _has_disadvantage(None) is False

    def test_empty_remarks(self) -> None:
        assert _has_disadvantage("") is False

    def test_disadvantage_keyword(self) -> None:
        assert _has_disadvantage("直線不利あり") is True

    def test_slipstart_keyword(self) -> None:
        assert _has_disadvantage("出遅れ") is True

    def test_s_contact_keyword(self) -> None:
        assert _has_disadvantage("S接触") is True

    def test_squeezed_inner(self) -> None:
        assert _has_disadvantage("内に張られ") is True

    def test_normal_remarks(self) -> None:
        assert _has_disadvantage("後方追走、直線伸びず") is False

    def test_scratch_not_disadvantage(self) -> None:
        """競走中止は不利キーワードに含まない。"""
        assert _has_disadvantage("競走中止") is False


class TestHasSlipstart:
    """出遅れキーワード検出テスト。"""

    def test_slipstart(self) -> None:
        assert _has_slipstart("出遅れ") is True

    def test_no_slipstart(self) -> None:
        assert _has_slipstart("直線不利") is False

    def test_none(self) -> None:
        assert _has_slipstart(None) is False


# ---------------------------------------------------------------------------
# _compute_score
# ---------------------------------------------------------------------------


class TestComputeScore:
    """スコア算出ロジックテスト。"""

    def test_no_disadvantage_returns_default(self) -> None:
        """不利なし → 50.0（中立）"""
        score = _compute_score(
            remarks="", finish_position=3, win_probability=0.2, n_horses=10,
            is_chronic_slipstart=False,
        )
        assert score == DEFAULT_SCORE

    def test_none_remarks_returns_default(self) -> None:
        """remarksなし → 50.0"""
        score = _compute_score(
            remarks=None, finish_position=5, win_probability=0.1, n_horses=12,
            is_chronic_slipstart=False,
        )
        assert score == DEFAULT_SCORE

    def test_chronic_slipstart_returns_reduced(self) -> None:
        """常習出遅れ → 40.0"""
        score = _compute_score(
            remarks="出遅れ", finish_position=8, win_probability=0.1, n_horses=10,
            is_chronic_slipstart=True,
        )
        assert score == CHRONIC_SLIPSTART_SCORE

    def test_slipstart_with_good_performance_no_bonus(self) -> None:
        """出遅れ + 期待通り好走 → 50.0（巻き返し候補にしない）"""
        # win_prob=0.5 → expected=5位、実際3位（乖離≤0）
        score = _compute_score(
            remarks="出遅れ",
            finish_position=3,
            win_probability=0.5,
            n_horses=10,
            is_chronic_slipstart=False,
        )
        assert score == DEFAULT_SCORE

    def test_slipstart_with_big_gap_applies_multiplier(self) -> None:
        """出遅れ + 大幅乖離 → 0.6倍係数が適用される"""
        # win_prob=0.5 → expected=5位、実際10位（乖離=5）
        # raw_bonus = min(50, 5*10) = 50 → 50*0.6=30 → score=80
        score = _compute_score(
            remarks="出遅れ",
            finish_position=10,
            win_probability=0.5,
            n_horses=10,
            is_chronic_slipstart=False,
        )
        assert score == 80.0

    def test_other_disadvantage_full_bonus(self) -> None:
        """出遅れ以外の不利 + 大幅乖離 → 係数1.0"""
        # win_prob=0.5 → expected=5位、実際10位（乖離=5）
        # raw_bonus = min(50, 5*10) = 50 → score=100
        score = _compute_score(
            remarks="直線不利",
            finish_position=10,
            win_probability=0.5,
            n_horses=10,
            is_chronic_slipstart=False,
        )
        assert score == 100.0

    def test_disadvantage_small_gap(self) -> None:
        """不利 + 小幅乖離（1着順）→ ボーナス10点"""
        # win_prob=0.5 → expected=5位、実際6位（乖離=1）
        # raw_bonus = min(50, 1*10) = 10 → score=60
        score = _compute_score(
            remarks="不利",
            finish_position=6,
            win_probability=0.5,
            n_horses=10,
            is_chronic_slipstart=False,
        )
        assert score == 60.0

    def test_no_win_probability_small_bonus(self) -> None:
        """win_probability なし + 不利あり → 小幅ボーナス（60.0）"""
        score = _compute_score(
            remarks="不利",
            finish_position=None,
            win_probability=None,
            n_horses=10,
            is_chronic_slipstart=False,
        )
        assert score == 60.0

    def test_max_score_capped_at_100(self) -> None:
        """スコアは100を超えない。"""
        score = _compute_score(
            remarks="不利",
            finish_position=18,
            win_probability=0.9,
            n_horses=18,
            is_chronic_slipstart=False,
        )
        assert score <= 100.0

    def test_min_score_capped_at_0(self) -> None:
        """スコアは0を下回らない。"""
        # CHRONIC_SLIPSTART_SCORE(40) は 0 以上のためテスト不要だが念のため
        score = _compute_score(
            remarks="出遅れ",
            finish_position=1,
            win_probability=0.9,
            n_horses=10,
            is_chronic_slipstart=True,
        )
        assert score >= 0.0


# ---------------------------------------------------------------------------
# ReboundIndexCalculator.calculate_batch（AsyncMock）
# ---------------------------------------------------------------------------


class TestReboundIndexCalculatorBatch:
    """calculate_batch の基本動作テスト（DB をモック）。"""

    @pytest.mark.asyncio
    async def test_empty_entries_returns_empty(self) -> None:
        """出走馬なし → 空dict を返す。"""
        mock_db = AsyncMock()

        # Race 取得
        race = MagicMock()
        race.date = "20260405"
        mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=race)

        # RaceEntry 取得 → 空
        entries_mock = MagicMock()
        entries_mock.scalars.return_value.all.return_value = []

        call_count = 0

        async def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                m = MagicMock()
                m.scalar_one_or_none.return_value = race
                return m
            m = MagicMock()
            m.scalars.return_value.all.return_value = []
            return m

        mock_db.execute.side_effect = side_effect

        calc = ReboundIndexCalculator(mock_db)
        result = await calc.calculate_batch(race_id=1)
        assert result == {}

    @pytest.mark.asyncio
    async def test_race_not_found_returns_empty(self) -> None:
        """Race が存在しない → 空dict を返す。"""
        mock_db = AsyncMock()
        m = MagicMock()
        m.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = m

        calc = ReboundIndexCalculator(mock_db)
        result = await calc.calculate_batch(race_id=999)
        assert result == {}
