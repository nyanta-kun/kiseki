"""払戻→組合せオッズ近似モデル ユニットテスト。

DB 接続不要。すべてオフライン動作。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.betting.odds_model import (
    OddsApproximator,
    _harville_place_probs,
    harville_combo_prob,
    harville_win_probs_from_odds,
)

# ---------------------------------------------------------------------------
# harville_win_probs_from_odds テスト
# ---------------------------------------------------------------------------


class TestHarvilleWinProbs:
    """harville_win_probs_from_odds のテスト。"""

    def test_sum_to_one(self) -> None:
        """正規化後の合計は 1.0。"""
        probs = harville_win_probs_from_odds([2.0, 5.0, 10.0])
        assert sum(probs) == pytest.approx(1.0, abs=1e-9)

    def test_lower_odds_higher_prob(self) -> None:
        """低オッズ → 高確率。"""
        probs = harville_win_probs_from_odds([2.0, 10.0, 100.0])
        assert probs[0] > probs[1] > probs[2]

    def test_equal_odds_equal_prob(self) -> None:
        """同一オッズなら等確率。"""
        probs = harville_win_probs_from_odds([5.0, 5.0, 5.0])
        assert probs[0] == pytest.approx(1.0 / 3, abs=1e-9)
        assert probs[1] == pytest.approx(1.0 / 3, abs=1e-9)
        assert probs[2] == pytest.approx(1.0 / 3, abs=1e-9)

    def test_single_horse(self) -> None:
        """1頭なら確率 1.0。"""
        probs = harville_win_probs_from_odds([3.0])
        assert probs[0] == pytest.approx(1.0, abs=1e-9)

    def test_none_odds_handled(self) -> None:
        """None オッズは 0 として扱う（正規化は有効馬分のみ）。"""
        probs = harville_win_probs_from_odds([2.0, None, 4.0])  # type: ignore[list-item]
        # index 0 と 2 にのみ確率
        assert probs[1] == pytest.approx(0.0, abs=1e-9)
        assert probs[0] > probs[2]
        assert probs[0] + probs[1] + probs[2] == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# harville_combo_prob テスト
# ---------------------------------------------------------------------------


class TestHarvilleComboProb:
    """harville_combo_prob のテスト。"""

    @pytest.fixture
    def win_probs_8h(self) -> list[float]:
        """8頭の均等確率（1/8 ずつ）。"""
        return [1.0 / 8] * 8

    @pytest.fixture
    def win_probs_4h(self) -> list[float]:
        """4頭の均等確率。"""
        return [1.0 / 4] * 4

    def test_win_prob(self, win_probs_8h: list[float]) -> None:
        """単勝確率は win_probs[i]。"""
        p = harville_combo_prob(win_probs_8h, [0], "win", 8)
        assert p == pytest.approx(1.0 / 8, abs=1e-9)

    def test_quinella_symmetry(self, win_probs_8h: list[float]) -> None:
        """馬連: P(i,j) = P(j,i)。"""
        p1 = harville_combo_prob(win_probs_8h, [0, 1], "quinella", 8)
        p2 = harville_combo_prob(win_probs_8h, [1, 0], "quinella", 8)
        assert p1 == pytest.approx(p2, abs=1e-12)

    def test_wide_symmetry(self, win_probs_8h: list[float]) -> None:
        """ワイド: P(i,j) = P(j,i)。"""
        p1 = harville_combo_prob(win_probs_8h, [0, 2], "wide", 8)
        p2 = harville_combo_prob(win_probs_8h, [2, 0], "wide", 8)
        assert p1 == pytest.approx(p2, abs=1e-12)

    def test_exacta_asymmetry(self, win_probs_8h: list[float]) -> None:
        """馬単: P(i→j) ≠ P(j→i) 一般的に（均等でも同じが自明でないことを確認）。"""
        p1 = harville_combo_prob(win_probs_8h, [0, 1], "exacta", 8)
        # 均等確率では馬単は対称
        p2 = harville_combo_prob(win_probs_8h, [1, 0], "exacta", 8)
        assert p1 == pytest.approx(p2, abs=1e-9)

    def test_trifecta_less_than_trio(self) -> None:
        """三連単 ≤ 三連複（特定順序 ≤ 順序不問）。"""
        probs = harville_win_probs_from_odds([3.0, 5.0, 8.0, 15.0, 20.0, 30.0, 40.0, 50.0])
        p_trio = harville_combo_prob(probs, [0, 1, 2], "trio", 8)
        p_trifecta = harville_combo_prob(probs, [0, 1, 2], "trifecta", 8)
        assert p_trifecta <= p_trio + 1e-9

    def test_trio_prob_in_range(self, win_probs_8h: list[float]) -> None:
        """三連複確率: 0〜1 の範囲。"""
        p = harville_combo_prob(win_probs_8h, [0, 1, 2], "trio", 8)
        assert 0.0 <= p <= 1.0

    def test_place_8_or_more_3within(self, win_probs_8h: list[float]) -> None:
        """8頭以上: 複勝 = 3着以内確率。全頭の複勝確率合計 ≈ 3.0。"""
        total = sum(
            harville_combo_prob(win_probs_8h, [i], "place", 8)
            for i in range(8)
        )
        assert total == pytest.approx(3.0, abs=0.05)

    def test_place_less_than_8_2within(self) -> None:
        """7頭以下: 複勝 = 2着以内確率。全頭の複勝確率合計 ≈ 2.0。"""
        probs = [1.0 / 7] * 7
        total = sum(
            harville_combo_prob(probs, [i], "place", 7)
            for i in range(7)
        )
        assert total == pytest.approx(2.0, abs=0.05)

    def test_unknown_bet_type(self, win_probs_8h: list[float]) -> None:
        """未知の券種は ValueError を送出する。"""
        with pytest.raises(ValueError, match="Unknown bet_type"):
            harville_combo_prob(win_probs_8h, [0], "unknown_bet", 8)


# ---------------------------------------------------------------------------
# _harville_place_probs テスト
# ---------------------------------------------------------------------------


class TestHarvillePlaceProbs:
    """_harville_place_probs のテスト（切り出し関数）。"""

    def test_all_in_range(self) -> None:
        """全確率が 0〜1 の範囲。"""
        probs = [0.4, 0.3, 0.2, 0.1]
        place = _harville_place_probs(probs, n=4)
        for p in place:
            assert 0.0 <= p <= 1.0

    def test_monotone_with_win_prob(self) -> None:
        """勝率が高い馬ほど複勝率も高い（単調性）。"""
        probs = [0.4, 0.3, 0.2, 0.1]
        place = _harville_place_probs(probs, n=4)
        assert place[0] > place[1] > place[2] > place[3]

    def test_place_ge_win(self) -> None:
        """複勝率 ≥ 勝率。"""
        probs = [0.3, 0.25, 0.2, 0.15, 0.1]
        place = _harville_place_probs(probs, n=5)
        for wp, pp in zip(probs, place):
            assert pp >= wp - 1e-9

    def test_sum_8horses(self) -> None:
        """8頭: 複勝確率合計 ≈ 3.0（3着以内）。"""
        probs = [1.0 / 8] * 8
        place = _harville_place_probs(probs, n=8)
        assert sum(place) == pytest.approx(3.0, abs=0.05)

    def test_sum_6horses(self) -> None:
        """6頭: 複勝確率合計 ≈ 2.0（2着以内）。"""
        probs = [1.0 / 6] * 6
        place = _harville_place_probs(probs, n=6)
        assert sum(place) == pytest.approx(2.0, abs=0.05)


# ---------------------------------------------------------------------------
# OddsApproximator テスト
# ---------------------------------------------------------------------------


class TestOddsApproximator:
    """OddsApproximator のテスト。"""

    @pytest.fixture
    def sample_params(self) -> dict:
        """サンプルパラメータ（単勝・馬連）。"""
        return {
            "win": {"a": -0.1, "b": 1.0, "mae": 0.05, "bias": 0.0, "n": 1000},
            "quinella": {"a": 0.05, "b": 0.95, "mae": 0.12, "bias": 0.01, "n": 500},
            "trio": {"a": 0.1, "b": 0.9, "mae": 0.20, "bias": 0.02, "n": 300},
        }

    @pytest.fixture
    def approx(self, sample_params: dict) -> OddsApproximator:
        """テスト用 OddsApproximator。"""
        return OddsApproximator(params=sample_params, version=1, fit_date="2025-01-01")

    def test_estimate_win_returns_positive(self, approx: OddsApproximator) -> None:
        """単勝の推定オッズは正の値。"""
        probs = harville_win_probs_from_odds([2.0, 5.0, 10.0, 20.0])
        est = approx.estimate("win", [0], probs)
        assert est > 0.0

    def test_estimate_win_higher_odds_for_longshot(
        self, approx: OddsApproximator
    ) -> None:
        """人気薄は人気馬より高い単勝推定オッズ。"""
        probs = harville_win_probs_from_odds([2.0, 5.0, 10.0, 20.0])
        est_fav = approx.estimate("win", [0], probs)  # 最低人気の馬
        est_long = approx.estimate("win", [3], probs)  # 最高人気の馬
        assert est_long > est_fav

    def test_estimate_minimum_one(self, approx: OddsApproximator) -> None:
        """推定オッズは最低 1.0 以上。"""
        probs = harville_win_probs_from_odds([1.01])  # ほぼ確実な単勝
        est = approx.estimate("win", [0], probs)
        assert est >= 1.0

    def test_estimate_unknown_type_fallback(self, approx: OddsApproximator) -> None:
        """未学習券種（exacta）はフォールバック（控除率ベース）を使う。"""
        probs = harville_win_probs_from_odds([3.0, 5.0, 8.0])
        est = approx.estimate("exacta", [0, 1], probs)
        assert est >= 1.0

    def test_estimate_naive_vs_estimate(self, approx: OddsApproximator) -> None:
        """ナイーブ推定と学習済み推定は値が異なりうる（両方とも ≥ 1.0）。"""
        probs = harville_win_probs_from_odds([3.0, 6.0, 12.0, 24.0])
        est = approx.estimate("quinella", [0, 1], probs)
        naive = approx.estimate_naive("quinella", [0, 1], probs)
        assert est >= 1.0
        assert naive >= 1.0

    def test_coverage(self, approx: OddsApproximator) -> None:
        """coverage() が学習済み券種リストを返す。"""
        cov = approx.coverage()
        assert "win" in cov
        assert "quinella" in cov

    def test_to_json_and_from_json(
        self, approx: OddsApproximator, tmp_path: Path
    ) -> None:
        """JSON 保存 → 読み込みで同一パラメータが復元される。"""
        json_path = tmp_path / "test_model.json"
        approx.to_json(json_path)
        loaded = OddsApproximator.from_json(json_path)

        assert loaded.version == approx.version
        assert loaded.fit_date == approx.fit_date
        for bt in approx.params:
            assert loaded.params[bt]["a"] == pytest.approx(
                approx.params[bt]["a"], abs=1e-9
            )
            assert loaded.params[bt]["b"] == pytest.approx(
                approx.params[bt]["b"], abs=1e-9
            )

    def test_from_json_not_found_raises(self) -> None:
        """存在しないファイルは FileNotFoundError を送出する。"""
        with pytest.raises(FileNotFoundError):
            OddsApproximator.from_json("/nonexistent/path/model.json")

    def test_estimate_trio_increases_with_harville_prob(
        self, approx: OddsApproximator
    ) -> None:
        """三連複: 人気馬の組合せは穴馬より低い推定オッズ。"""
        probs = harville_win_probs_from_odds(
            [2.0, 3.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0]
        )
        est_pop = approx.estimate("trio", [0, 1, 2], probs, n_horses=8)
        est_long = approx.estimate("trio", [5, 6, 7], probs, n_horses=8)
        assert est_long > est_pop


# ---------------------------------------------------------------------------
# 数値的整合性テスト
# ---------------------------------------------------------------------------


class TestNumericalConsistency:
    """数値的整合性の回帰テスト。"""

    def test_harville_win_consistent_with_input_odds(self) -> None:
        """単勝オッズから変換した勝率は、ほぼ 1/odds に比例する。"""
        odds = [2.0, 5.0, 10.0]
        probs = harville_win_probs_from_odds(odds)
        raw = [1.0 / o for o in odds]
        total = sum(raw)
        expected = [r / total for r in raw]
        for p, e in zip(probs, expected):
            assert p == pytest.approx(e, abs=1e-12)

    def test_quinella_equals_sum_of_two_exactas(self) -> None:
        """馬連 = 馬単(i→j) + 馬単(j→i)。"""
        probs = harville_win_probs_from_odds([3.0, 5.0, 8.0, 15.0, 20.0])
        p_q = harville_combo_prob(probs, [0, 1], "quinella", 5)
        p_e1 = harville_combo_prob(probs, [0, 1], "exacta", 5)
        p_e2 = harville_combo_prob(probs, [1, 0], "exacta", 5)
        assert p_q == pytest.approx(p_e1 + p_e2, abs=1e-9)

    def test_trio_sum_less_than_sum_of_six_exactas(self) -> None:
        """三連複 = 6通りの三連単の合計（Harville 近似）。"""
        probs = harville_win_probs_from_odds([3.0, 5.0, 8.0, 15.0, 20.0, 30.0, 50.0, 100.0])
        p_trio = harville_combo_prob(probs, [0, 1, 2], "trio", 8)
        # 6通りの三連単
        from itertools import permutations
        p_sum = sum(
            harville_combo_prob(probs, list(perm), "trifecta", 8)
            for perm in permutations([0, 1, 2])
        )
        assert p_trio == pytest.approx(p_sum, abs=1e-9)

    def test_naive_odds_formula(self) -> None:
        """ナイーブ推定: estimate_naive = (1 - takeout) / p_harville。"""
        approx = OddsApproximator(params={}, version=1)
        probs = harville_win_probs_from_odds([3.0, 7.0, 12.0])
        p = harville_combo_prob(probs, [0], "win", 3)
        naive = approx.estimate_naive("win", [0], probs, 3)
        expected = (1.0 - 0.20) / p  # takeout 20%
        assert naive == pytest.approx(expected, abs=1e-6)
