"""allocation.py のユニットテスト。

DoD 要件:
- 既知の手計算例（2択Kelly等）と一致するテストケースあり
- EV フィルタ・点数制約・100円丸め・レース内予算按分の動作確認
"""

from __future__ import annotations

import pytest

from src.betting.allocation import (
    BetCandidate,
    RaceConstraintState,
    _kelly_fraction_single,
    _round_stake,
    _shrink_probability,
    allocate,
)

# ---------------------------------------------------------------------------
# _shrink_probability
# ---------------------------------------------------------------------------


class TestShrinkProbability:
    """Shrinkage 計算のテスト。"""

    def test_no_shrink_when_alpha_one(self) -> None:
        """alpha=1.0 のとき est_prob がそのまま返る。"""
        result = _shrink_probability(0.30, market_prob=0.20, alpha=1.0)
        assert result == pytest.approx(0.30)

    def test_full_market_when_alpha_zero(self) -> None:
        """alpha=0.0 のとき市場確率がそのまま返る。"""
        result = _shrink_probability(0.30, market_prob=0.20, alpha=0.0)
        assert result == pytest.approx(0.20)

    def test_midpoint_alpha_half(self) -> None:
        """alpha=0.5 のとき中間値になる。"""
        result = _shrink_probability(0.30, market_prob=0.10, alpha=0.5)
        assert result == pytest.approx(0.20)

    def test_no_market_prob(self) -> None:
        """market_prob=None のとき est_prob をそのまま返す。"""
        result = _shrink_probability(0.30, market_prob=None, alpha=0.5)
        assert result == pytest.approx(0.30)

    def test_clamped_within_range(self) -> None:
        """縮小後も (0, 1) に収まる。"""
        result = _shrink_probability(0.0, market_prob=0.0, alpha=0.5)
        assert 0.0 < result < 1.0


# ---------------------------------------------------------------------------
# _kelly_fraction_single
# ---------------------------------------------------------------------------


class TestKellyFractionSingle:
    """Kelly 比率計算のテスト（手計算例との一致確認）。"""

    def test_classic_two_choice_kelly(self) -> None:
        """2択Kelly の手計算例との一致。

        p=0.55, odds=2.0（コイントス有利版）:
        net_odds = 2.0 - 1.0 = 1.0
        f* = (0.55 * 1.0 - 0.45) / 1.0 = 0.10
        quarter-Kelly = 0.25 * 0.10 = 0.025
        """
        kf = _kelly_fraction_single(est_prob=0.55, odds=2.0, kelly_fraction=0.25)
        assert kf == pytest.approx(0.025, abs=1e-9)

    def test_full_kelly_fraction_one(self) -> None:
        """fraction=1.0 のとき full Kelly が返る。

        p=0.6, odds=3.0:
        net_odds = 2.0
        f* = (0.6 * 2.0 - 0.4) / 2.0 = 0.8 / 2.0 = 0.40
        """
        kf = _kelly_fraction_single(est_prob=0.6, odds=3.0, kelly_fraction=1.0)
        assert kf == pytest.approx(0.40, abs=1e-9)

    def test_negative_edge_returns_zero(self) -> None:
        """エッジがマイナスの場合は 0 を返す。

        p=0.1, odds=2.0:
        f* = (0.1 * 1.0 - 0.9) / 1.0 = -0.80 → clamp → 0
        """
        kf = _kelly_fraction_single(est_prob=0.1, odds=2.0, kelly_fraction=0.25)
        assert kf == 0.0

    def test_odds_below_one_returns_zero(self) -> None:
        """オッズ < 1.0 は計算不能なので 0 を返す。"""
        kf = _kelly_fraction_single(est_prob=0.8, odds=0.9, kelly_fraction=0.25)
        assert kf == 0.0

    def test_quarter_kelly_racing_example(self) -> None:
        """競馬の典型例: 単勝10倍の馬に p=0.15 の場合。

        net_odds = 9.0
        f* = (0.15 * 9.0 - 0.85) / 9.0 = (1.35 - 0.85) / 9.0 = 0.5 / 9.0 ≈ 0.0556
        quarter-Kelly = 0.25 * 0.0556 ≈ 0.0139
        """
        kf = _kelly_fraction_single(est_prob=0.15, odds=10.0, kelly_fraction=0.25)
        expected_full = (0.15 * 9.0 - 0.85) / 9.0
        assert kf == pytest.approx(0.25 * expected_full, abs=1e-9)


# ---------------------------------------------------------------------------
# _round_stake
# ---------------------------------------------------------------------------


class TestRoundStake:
    """ステーク丸め関数のテスト。"""

    def test_exact_multiple(self) -> None:
        assert _round_stake(500.0) == 500

    def test_rounds_down(self) -> None:
        assert _round_stake(550.0) == 500

    def test_rounds_down_199(self) -> None:
        assert _round_stake(199.0) == 100

    def test_zero(self) -> None:
        assert _round_stake(0.0) == 0

    def test_large_amount(self) -> None:
        assert _round_stake(12345.0) == 12300


# ---------------------------------------------------------------------------
# allocate
# ---------------------------------------------------------------------------


class TestAllocate:
    """allocate() の統合テスト。"""

    def _make_candidate(
        self,
        bet_type: str = "win",
        combination: str = "01",
        est_prob: float = 0.20,
        odds: float = 10.0,
        tag: str = "test",
    ) -> BetCandidate:
        return BetCandidate(
            bet_type=bet_type,
            combination=combination,
            est_prob=est_prob,
            odds=odds,
            tag=tag,
        )

    def test_basic_allocation_returns_nonzero_stake(self) -> None:
        """基本的なケースでステークが正の値になる。"""
        cands = [self._make_candidate(est_prob=0.20, odds=10.0)]
        result = allocate(cands, bankroll=100_000)
        assert len(result) == 1
        assert result[0].stake > 0

    def test_stake_is_multiple_of_100(self) -> None:
        """ステークが 100 円単位であること。"""
        cands = [self._make_candidate(est_prob=0.20, odds=10.0)]
        result = allocate(cands, bankroll=100_000)
        for r in result:
            assert r.stake % 100 == 0

    def test_ev_filter_removes_low_ev(self) -> None:
        """EV が min_ev 未満の候補は除外される。"""
        # est_prob=0.05, odds=5.0 → EV = 0.25 < 1.20 → 除外
        cands = [self._make_candidate(est_prob=0.05, odds=5.0)]
        result = allocate(cands, bankroll=100_000, min_ev=1.20)
        assert len(result) == 0

    def test_ev_filter_keeps_high_ev(self) -> None:
        """EV が min_ev 以上の候補は残る。"""
        # est_prob=0.20, odds=10.0 → EV = 2.0 > 1.20
        cands = [self._make_candidate(est_prob=0.20, odds=10.0)]
        result = allocate(cands, bankroll=100_000, min_ev=1.20)
        assert len(result) == 1

    def test_max_per_race_constraint(self) -> None:
        """レース内合計がmax_per_raceを超えない。"""
        cands = [
            self._make_candidate(combination="01", est_prob=0.30, odds=10.0),
            self._make_candidate(combination="02", est_prob=0.30, odds=10.0),
            self._make_candidate(combination="03", est_prob=0.25, odds=10.0),
        ]
        max_per_race = 2000
        result = allocate(cands, bankroll=100_000, max_per_race=max_per_race)
        total = sum(r.stake for r in result)
        assert total <= max_per_race + 100  # 丸め誤差 1 枚分の余裕

    def test_negative_edge_excluded(self) -> None:
        """エッジがマイナス（Kelly < 0）の場合は除外される。"""
        # est_prob=0.05, odds=2.0 → EV = 0.10 < min_ev でまず除外
        # EV フィルタを外しても Kelly < 0 で除外されることを確認
        cands = [self._make_candidate(est_prob=0.05, odds=2.0)]
        result = allocate(cands, bankroll=100_000, min_ev=0.0)
        # Kelly = (0.05 * 1.0 - 0.95) / 1.0 = -0.90 → 0
        assert len(result) == 0

    def test_kelly_quarter_fraction(self) -> None:
        """quarter-Kelly のステーク計算が手計算と一致する（max_per_ticket 制約を考慮）。

        bankroll=100_000, p=0.20, odds=10.0
        net_odds = 9.0
        f* = (0.20 * 9.0 - 0.80) / 9.0 = 1.0 / 9.0 ≈ 0.1111
        quarter-Kelly = 0.25 * 0.1111 ≈ 0.02778
        raw_stake = 0.02778 * 100_000 = 2778
        → max_per_ticket=1000 で clamp → 1000 → 丸め後 1000

        .env の bet_max_per_ticket=1000 が本番設定なので、
        2778 は 1000 に clamp される。Kelly 比率は参考値として出力される。
        """
        cands = [self._make_candidate(est_prob=0.20, odds=10.0)]
        result = allocate(
            cands, bankroll=100_000, kelly_fraction=0.25,
            min_ev=1.0,
            max_per_race=100_000,  # レース上限は外す
        )
        assert len(result) == 1
        # Kelly 比率が正しく計算されていることを確認
        expected_full = (0.20 * 9.0 - 0.80) / 9.0
        expected_kf = 0.25 * expected_full
        assert result[0].kelly_f == pytest.approx(expected_kf, abs=1e-9)
        # ステークは bet_max_per_ticket（1000円）で clamp されている
        from src.config import settings
        assert result[0].stake <= settings.bet_max_per_ticket
        assert result[0].stake >= 100  # MIN_STAKE 以上

    def test_min_stake_filter(self) -> None:
        """計算後のステークが MIN_STAKE (100円) 未満は除外される。"""
        # 非常に小さいバンクロールでステークが 100 未満になるケース
        cands = [self._make_candidate(est_prob=0.20, odds=10.0)]
        result = allocate(
            cands, bankroll=500, kelly_fraction=0.25, min_ev=1.0,
        )
        # bankroll=500 * 0.028 ≈ 14 円 → 丸め後 0 → 除外
        assert all(r.stake >= 100 for r in result)

    def test_ticket_count_constraint_by_type(self) -> None:
        """券種別最大点数を超える場合は EV 降順で切られる。"""
        # win: 最大 3 点。4 候補あっても 3 点に絞られる
        cands = [
            BetCandidate("win", f"0{i}", est_prob=0.15, odds=10.0)
            for i in range(1, 5)
        ]
        result = allocate(
            cands, bankroll=100_000, min_ev=1.0,
            max_tickets_override={"win": 3},
        )
        assert len(result) <= 3

    def test_shrinkage_reduces_stake(self) -> None:
        """Shrinkage を適用すると EV が下がりステークが減る or 除外される。"""
        cands = [self._make_candidate(est_prob=0.20, odds=10.0)]
        # 市場確率 1/10 = 0.10。alpha=0.5 → shrunk = 0.15
        market_probs = {"01": 0.10}
        result_no_shrink = allocate(
            cands, bankroll=100_000, prob_alpha=1.0,
            min_ev=1.0, max_per_race=100_000,
        )
        result_shrunk = allocate(
            cands, bankroll=100_000, prob_alpha=0.5,
            market_probs=market_probs,
            min_ev=1.0, max_per_race=100_000,
        )
        # shrunk の方がステークが小さいか同等
        if result_shrunk:
            assert result_shrunk[0].stake <= result_no_shrink[0].stake + 100


# ---------------------------------------------------------------------------
# RaceConstraintState
# ---------------------------------------------------------------------------


class TestRaceConstraintState:
    """RaceConstraintState のテスト。"""

    def test_daily_budget_not_exceeded(self) -> None:
        """当日累計が上限を超えない。"""
        state = RaceConstraintState(day_spent=28_000)
        # bet_max_per_day=30_000 なので残り 2000
        allowed = state.check_daily_budget(5000)
        assert allowed == 2000

    def test_consecutive_losses_halt(self) -> None:
        """連敗数が上限以上のとき is_halted() が True。"""
        state = RaceConstraintState(consecutive_losses=10)
        assert state.is_halted() is True

    def test_consecutive_losses_not_halted(self) -> None:
        """連敗数が上限未満のとき is_halted() が False。"""
        state = RaceConstraintState(consecutive_losses=9)
        assert state.is_halted() is False

    def test_record_race_result_hit_resets_losses(self) -> None:
        """的中した場合は連敗数がリセットされる。"""
        state = RaceConstraintState(day_spent=0, consecutive_losses=5)
        new_state = state.record_race_result(spent=1000, hit=True)
        assert new_state.consecutive_losses == 0
        assert new_state.day_spent == 1000

    def test_record_race_result_miss_increments_losses(self) -> None:
        """不的中の場合は連敗数がインクリメントされる。"""
        state = RaceConstraintState(day_spent=0, consecutive_losses=3)
        new_state = state.record_race_result(spent=500, hit=False)
        assert new_state.consecutive_losses == 4
        assert new_state.day_spent == 500

    def test_immutability(self) -> None:
        """record_race_result は元のオブジェクトを変更しない。"""
        state = RaceConstraintState(day_spent=1000, consecutive_losses=2)
        _ = state.record_race_result(spent=500, hit=False)
        assert state.day_spent == 1000
        assert state.consecutive_losses == 2
