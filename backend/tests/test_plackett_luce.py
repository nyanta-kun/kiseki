"""Plackett-Luce 複勝確率算出のユニットテスト（研究用スクリプト）

DB接続不要。小さな合成例（3〜6頭）で、全順列を明示的に列挙するブルートフォース計算
（Plackett-Luceの逐次選択確率をitertools.permutationsで直接積み上げる独立実装）と
`plackett_luce_place_probs` の出力が一致することを検証する。

また、既に本番で稼働している v24系 Harville 実装
（`src/indices/composite.py::CompositeIndexCalculator._harville_place_probs`、
数式的には Plackett-Luce と同一）とのクロスチェックも行う。

最重要チェック: 全馬の P(3着以内) 合計が 3.0 に近いか
（3着以内に入るのは必ず3頭なので、Plackett-Luce実装が正しければレース全体で
合計は理論上ちょうど 3.0 になる）。
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from scripts.jra_place_probability_plackett_luce import (
    calib_metrics,
    heuristic_place_probs,
    plackett_luce_place_probs,
    softmax,
)
from src.indices.composite import CompositeIndexCalculator

# ---------------------------------------------------------------------------
# ブルートフォース（全順列列挙）による独立検証
# ---------------------------------------------------------------------------


def _brute_force_top3_probs(worths: np.ndarray) -> np.ndarray:
    """全順列を列挙して Plackett-Luce の P(3着以内) を直接計算する（テスト専用の対照実装）。

    n! 通りの完全順列それぞれについて、逐次選択の連鎖確率
    (w_order[0]/W) * (w_order[1]/(W-w_order[0])) * ... を計算し、
    各馬について「その馬が上位3着以内に現れる順列」の確率を合算する。
    n<=8 程度でのみ現実的（テストでは n<=6 のみ使用）。
    """
    w = np.asarray(worths, dtype=float)
    n = len(w)
    total = w.sum()
    acc = np.zeros(n)
    for order in itertools.permutations(range(n)):
        prob = 1.0
        remaining = total
        for pos in range(n):
            idx = order[pos]
            prob *= w[idx] / remaining
            remaining -= w[idx]
            if prob == 0.0:
                break
        top3 = order[: min(3, n)]
        for idx in top3:
            acc[idx] += prob
    return acc


class TestBruteForceCrossCheck:
    """全順列列挙との一致検証（3〜6頭の合成例）。"""

    def test_four_horses_matches_brute_force(self) -> None:
        worths = np.array([0.40, 0.30, 0.20, 0.10])
        expected = _brute_force_top3_probs(worths)
        actual = plackett_luce_place_probs(worths)
        np.testing.assert_allclose(actual, expected, atol=1e-10)

    def test_five_horses_matches_brute_force(self) -> None:
        worths = np.array([0.35, 0.25, 0.20, 0.12, 0.08])
        expected = _brute_force_top3_probs(worths)
        actual = plackett_luce_place_probs(worths)
        np.testing.assert_allclose(actual, expected, atol=1e-10)

    def test_six_horses_uneven_weights_matches_brute_force(self) -> None:
        worths = np.array([0.50, 0.20, 0.10, 0.09, 0.06, 0.05])
        expected = _brute_force_top3_probs(worths)
        actual = plackett_luce_place_probs(worths)
        np.testing.assert_allclose(actual, expected, atol=1e-10)

    def test_six_horses_near_uniform_matches_brute_force(self) -> None:
        rng = np.random.default_rng(42)
        worths = rng.dirichlet(np.ones(6))
        expected = _brute_force_top3_probs(worths)
        actual = plackett_luce_place_probs(worths)
        np.testing.assert_allclose(actual, expected, atol=1e-10)

    def test_scale_invariance(self) -> None:
        """worths を定数倍しても結果は変わらない（PLモデルはスケール不変）。"""
        worths = np.array([4.0, 3.0, 2.0, 1.0, 0.5])
        p_a = plackett_luce_place_probs(worths)
        p_b = plackett_luce_place_probs(worths * 100.0)
        np.testing.assert_allclose(p_a, p_b, atol=1e-10)


# ---------------------------------------------------------------------------
# 健全性チェック: 全馬の P(3着以内) 合計が 3.0 に近いか
# ---------------------------------------------------------------------------


class TestSumToThree:
    """Plackett-Luce実装の最重要整合性チェック。"""

    @pytest.mark.parametrize("n", [3, 4, 5, 6, 8, 10, 14, 18])
    def test_sum_equals_three_for_various_head_counts(self, n: int) -> None:
        rng = np.random.default_rng(n)
        worths = rng.dirichlet(np.ones(n))
        place = plackett_luce_place_probs(worths)
        expected_sum = min(3.0, float(n))
        assert place.sum() == pytest.approx(expected_sum, abs=1e-8)

    def test_sum_equals_three_uniform_worths(self) -> None:
        """全馬が同じ強さの場合でも合計は3.0（対称性チェック）。"""
        worths = np.ones(12) / 12
        place = plackett_luce_place_probs(worths)
        assert place.sum() == pytest.approx(3.0, abs=1e-8)

    def test_sum_equals_three_skewed_worths(self) -> None:
        """1頭が圧倒的に強い極端なケースでも合計は3.0。"""
        worths = np.array([0.90, 0.02, 0.02, 0.02, 0.02, 0.02])
        place = plackett_luce_place_probs(worths)
        assert place.sum() == pytest.approx(3.0, abs=1e-8)

    def test_two_horses_sum_equals_two(self) -> None:
        """2頭立てなら全馬が必ず3着以内（=2着以内）→ 合計2.0"""
        place = plackett_luce_place_probs(np.array([0.6, 0.4]))
        assert place.sum() == pytest.approx(2.0, abs=1e-9)
        np.testing.assert_allclose(place, [1.0, 1.0])


# ---------------------------------------------------------------------------
# 基本性質
# ---------------------------------------------------------------------------


class TestPlackettLuceBasicProperties:
    def test_all_probs_between_0_and_1(self) -> None:
        worths = np.array([0.5, 0.2, 0.15, 0.1, 0.05])
        for p in plackett_luce_place_probs(worths):
            assert 0.0 <= p <= 1.0

    def test_higher_worth_higher_place_prob(self) -> None:
        """強さが大きい馬ほど複勝確率も高い（単調性）"""
        worths = np.array([0.4, 0.3, 0.2, 0.1])
        place = plackett_luce_place_probs(worths)
        assert place[0] > place[1] > place[2] > place[3]

    def test_single_horse_is_certain(self) -> None:
        place = plackett_luce_place_probs(np.array([1.0]))
        assert place[0] == pytest.approx(1.0)

    def test_place_ge_win(self) -> None:
        """複勝確率 >= 勝率（3着以内は1着より広い条件）"""
        worths = np.array([0.35, 0.25, 0.2, 0.12, 0.08])
        win_p = worths / worths.sum()
        place = plackett_luce_place_probs(worths)
        for wp, pp in zip(win_p, place):
            assert pp >= wp - 1e-9

    def test_empty_input(self) -> None:
        assert len(plackett_luce_place_probs(np.array([]))) == 0

    def test_zero_total_raises(self) -> None:
        with pytest.raises(ValueError):
            plackett_luce_place_probs(np.array([0.0, 0.0, 0.0, 0.0]))


# ---------------------------------------------------------------------------
# 既存本番実装（v24 Harville）とのクロスチェック
# ---------------------------------------------------------------------------


class TestCrossCheckAgainstProductionHarville:
    """`src/indices/composite.py` の既存 Harville 実装（v24, 稼働中）は数式的に
    Plackett-Luce と同一である。本スクリプトの独立実装が一致することを確認する。
    """

    def test_matches_production_harville_8_horses(self) -> None:
        win_probs = [0.22, 0.18, 0.15, 0.12, 0.10, 0.09, 0.08, 0.06]
        prod = CompositeIndexCalculator._harville_place_probs(win_probs)
        mine = plackett_luce_place_probs(np.array(win_probs))
        np.testing.assert_allclose(mine, np.array(prod), atol=1e-9)

    def test_matches_production_harville_16_horses(self) -> None:
        rng = np.random.default_rng(7)
        win_probs = rng.dirichlet(np.ones(16))
        prod = CompositeIndexCalculator._harville_place_probs(list(win_probs))
        mine = plackett_luce_place_probs(win_probs)
        np.testing.assert_allclose(mine, np.array(prod), atol=1e-9)

    def test_matches_production_harville_from_composite_index(self) -> None:
        """composite_index → softmax → Plackett-Luce の一連の流れで一致すること。"""
        scores = np.array([72.0, 65.0, 60.0, 58.0, 50.0, 45.0, 40.0, 35.0, 30.0])
        win_p = softmax(scores)
        prod = CompositeIndexCalculator._harville_place_probs(list(win_p))
        mine = plackett_luce_place_probs(win_p)
        np.testing.assert_allclose(mine, np.array(prod), atol=1e-9)


# ---------------------------------------------------------------------------
# softmax / heuristic_place_probs
# ---------------------------------------------------------------------------


class TestSoftmax:
    def test_sums_to_one(self) -> None:
        scores = np.array([60.0, 55.0, 50.0, 45.0])
        probs = softmax(scores)
        assert probs.sum() == pytest.approx(1.0, abs=1e-9)

    def test_single_horse(self) -> None:
        assert softmax(np.array([50.0]))[0] == pytest.approx(1.0)


class TestHeuristicPlaceProbs:
    def test_triples_win_probability(self) -> None:
        win_p = np.array([0.1, 0.05])
        place = heuristic_place_probs(win_p)
        np.testing.assert_allclose(place, [0.3, 0.15])

    def test_clips_at_one(self) -> None:
        win_p = np.array([0.5, 0.4])
        place = heuristic_place_probs(win_p)
        np.testing.assert_allclose(place, [1.0, 1.0])

    def test_heuristic_can_exceed_true_place_prob(self) -> None:
        """人気薄・多頭数では win_p×3 が理論値(Plackett-Luce)から乖離しうることを確認。

        頭数が多く、1頭が突出して強いレースでは、下位馬の win_p×3 は
        Plackett-Luce の理論値より値が離れうる（過大/過小どちらの向きもあり得る）。
        """
        worths = np.array([0.55] + [0.45 / 13] * 13)  # 14頭・1頭突出
        win_p = worths / worths.sum()
        heur = heuristic_place_probs(win_p)
        pl = plackett_luce_place_probs(worths)
        # 突出馬(index 0)以外では単純×3ヒューリスティックとPL理論値に有意な乖離がある
        diffs = np.abs(heur[1:] - pl[1:])
        assert diffs.max() > 0.01


# ---------------------------------------------------------------------------
# calib_metrics (ECE)
# ---------------------------------------------------------------------------


class TestCalibMetrics:
    def test_perfect_calibration_has_zero_ece(self) -> None:
        rng = np.random.default_rng(0)
        n = 5000
        prob = rng.uniform(0.05, 0.95, n)
        y = (rng.uniform(0, 1, n) < prob).astype(int)
        m = calib_metrics(prob, y, n_bins=10)
        assert m["ece"] < 0.02

    def test_overconfident_predictions_have_positive_ece(self) -> None:
        """予測が実測より常に高い(過信)場合、ECEは有意に正になる。"""
        rng = np.random.default_rng(1)
        n = 5000
        true_p = rng.uniform(0.05, 0.5, n)
        overconfident_p = np.clip(true_p * 1.8, 0, 1)
        y = (rng.uniform(0, 1, n) < true_p).astype(int)
        m = calib_metrics(overconfident_p, y, n_bins=10)
        assert m["ece"] > 0.05

    def test_empty_input_returns_nan(self) -> None:
        m = calib_metrics(np.array([]), np.array([]))
        assert m["n"] == 0
