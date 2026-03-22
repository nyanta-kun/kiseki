"""総合指数 確率算出 ユニットテスト

Softmax による勝率変換と Harville 公式による複勝率算出を検証する。
DB 接続不要。
"""

from __future__ import annotations

import math

import pytest

from src.indices.composite import CompositeIndexCalculator


# ---------------------------------------------------------------------------
# Softmax テスト
# ---------------------------------------------------------------------------


class TestSoftmax:
    """_softmax の動作テスト。"""

    def test_probabilities_sum_to_one(self) -> None:
        """全馬の勝率合計は 1.0"""
        scores = [60.0, 55.0, 50.0, 45.0, 40.0]
        probs = CompositeIndexCalculator._softmax(scores)
        assert sum(probs) == pytest.approx(1.0, abs=1e-9)

    def test_higher_score_higher_prob(self) -> None:
        """スコアが高い馬ほど勝率が高い"""
        scores = [70.0, 60.0, 50.0]
        probs = CompositeIndexCalculator._softmax(scores)
        assert probs[0] > probs[1] > probs[2]

    def test_equal_scores_equal_probs(self) -> None:
        """全馬同スコアなら等確率"""
        scores = [50.0, 50.0, 50.0, 50.0]
        probs = CompositeIndexCalculator._softmax(scores)
        expected = 1.0 / 4
        for p in probs:
            assert p == pytest.approx(expected, abs=1e-9)

    def test_single_horse_wins_certainly(self) -> None:
        """1頭なら勝率 1.0"""
        probs = CompositeIndexCalculator._softmax([60.0])
        assert probs[0] == pytest.approx(1.0)

    def test_all_probs_between_0_and_1(self) -> None:
        """全確率が 0〜1 の範囲"""
        scores = [80.0, 50.0, 20.0]
        for p in CompositeIndexCalculator._softmax(scores):
            assert 0.0 <= p <= 1.0

    def test_large_score_diff_dominates(self) -> None:
        """圧倒的スコア差 → 高スコア馬の確率が他馬を大きく上回る"""
        scores = [100.0] + [50.0] * 17  # 18頭
        probs = CompositeIndexCalculator._softmax(scores)
        assert probs[0] > sum(probs[1:])


# ---------------------------------------------------------------------------
# Harville 複勝率テスト
# ---------------------------------------------------------------------------


class TestHarvillePlaceProbs:
    """_harville_place_probs の動作テスト。"""

    def test_place_probs_between_0_and_1(self) -> None:
        """全複勝率が 0〜1 の範囲"""
        win_probs = [0.4, 0.3, 0.2, 0.1]
        for p in CompositeIndexCalculator._harville_place_probs(win_probs):
            assert 0.0 <= p <= 1.0

    def test_higher_win_prob_higher_place_prob(self) -> None:
        """勝率が高い馬ほど複勝率も高い（単調性）"""
        win_probs = [0.4, 0.3, 0.2, 0.1]
        place = CompositeIndexCalculator._harville_place_probs(win_probs)
        assert place[0] > place[1] > place[2] > place[3]

    def test_place_ge_win(self) -> None:
        """複勝率 ≥ 勝率（3着以内は1着より広い条件）"""
        win_probs = [0.3, 0.25, 0.2, 0.15, 0.1]
        place = CompositeIndexCalculator._harville_place_probs(win_probs)
        for wp, pp in zip(win_probs, place):
            assert pp >= wp - 1e-9

    def test_three_horses_place_prob_high(self) -> None:
        """3頭レースでは各馬の複勝率が高い（全馬3着以内）"""
        win_probs = [0.5, 0.3, 0.2]
        place = CompositeIndexCalculator._harville_place_probs(win_probs)
        # 3頭なら全員3着以内なので全員 ≈ 1.0
        for pp in place:
            assert pp == pytest.approx(1.0, abs=1e-6)

    def test_single_horse_place_is_one(self) -> None:
        """1頭なら複勝率 1.0"""
        place = CompositeIndexCalculator._harville_place_probs([1.0])
        assert place[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _attach_probabilities 統合テスト
# ---------------------------------------------------------------------------


class TestAttachProbabilities:
    """_attach_probabilities が results を正しく更新するテスト。"""

    def _make_results(self, composites: list[float]) -> list[dict]:
        return [{"horse_id": i + 1, "composite_index": c} for i, c in enumerate(composites)]

    def test_keys_added(self) -> None:
        """win_probability / place_probability キーが追加される"""
        results = self._make_results([60.0, 55.0, 50.0])
        CompositeIndexCalculator._attach_probabilities(results)
        for r in results:
            assert "win_probability" in r
            assert "place_probability" in r

    def test_win_probs_sum_to_one(self) -> None:
        """全馬の勝率合計が 1.0"""
        results = self._make_results([70.0, 60.0, 50.0, 40.0])
        CompositeIndexCalculator._attach_probabilities(results)
        total = sum(r["win_probability"] for r in results)
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_place_ge_win_in_results(self) -> None:
        """複勝率 ≥ 勝率"""
        results = self._make_results([65.0, 55.0, 50.0, 45.0, 40.0])
        CompositeIndexCalculator._attach_probabilities(results)
        for r in results:
            assert r["place_probability"] >= r["win_probability"] - 1e-6

    def test_top_horse_highest_probabilities(self) -> None:
        """最高スコア馬が最高の勝率・複勝率"""
        results = self._make_results([80.0, 60.0, 55.0, 50.0])
        CompositeIndexCalculator._attach_probabilities(results)
        assert results[0]["win_probability"] == max(r["win_probability"] for r in results)
        assert results[0]["place_probability"] == max(r["place_probability"] for r in results)

    def test_probabilities_rounded_to_4_decimals(self) -> None:
        """確率は小数点4桁に丸められている"""
        results = self._make_results([60.0, 55.0, 50.0])
        CompositeIndexCalculator._attach_probabilities(results)
        for r in results:
            wp = r["win_probability"]
            pp = r["place_probability"]
            assert wp == round(wp, 4)
            assert pp == round(pp, 4)
