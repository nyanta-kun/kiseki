"""T03: 着順確率モデル ユニットテスト

確認項目:
  1. harville 手法で全組合せ確率の和 ≈ 1.0（券種別）
  2. 対称性（馬連は順不同）
  3. composite.py の Harville 実装との数値一致（小例）
  4. henery λ=1.0 のとき harville と同一
  5. 既知の小例で手計算値と一致
"""

from __future__ import annotations

import pytest

from src.betting.finish_order import (
    _harville_joint,
    _harville_place_prob_single,
    _henery_adjusted,
    _normalize,
    combo_probability,
    enumerate_combo_probs,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

WIN_PROBS_4 = {1: 0.40, 2: 0.30, 3: 0.20, 4: 0.10}  # 合計 1.0
WIN_PROBS_8 = {
    1: 0.25, 2: 0.20, 3: 0.15, 4: 0.15,
    5: 0.10, 6: 0.07, 7: 0.05, 8: 0.03,
}  # 合計 1.0
WIN_PROBS_3 = {1: 0.50, 2: 0.30, 3: 0.20}  # 3頭

# ---------------------------------------------------------------------------
# 1. 正規化テスト
# ---------------------------------------------------------------------------


class TestNormalize:
    """_normalize の動作確認。"""

    def test_already_normalized(self) -> None:
        """合計が 1.0 ならそのまま返す。"""
        d = {1: 0.6, 2: 0.4}
        n = _normalize(d)
        assert sum(n.values()) == pytest.approx(1.0, abs=1e-9)

    def test_not_normalized(self) -> None:
        """合計が 1.0 でなくても正規化される。"""
        d = {1: 2.0, 2: 3.0}
        n = _normalize(d)
        assert sum(n.values()) == pytest.approx(1.0, abs=1e-9)
        assert n[1] == pytest.approx(0.4, abs=1e-9)
        assert n[2] == pytest.approx(0.6, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. Harville 同時確率テスト
# ---------------------------------------------------------------------------


class TestHarvilleJoint:
    """_harville_joint の動作確認。"""

    def test_sum_of_all_permutations_is_one(self) -> None:
        """3頭全順列の確率の和 = 1.0（単純な事例）。"""
        import itertools
        wp = {1: 0.5, 2: 0.3, 3: 0.2}
        total = sum(
            _harville_joint(wp, perm)
            for perm in itertools.permutations(wp.keys())
        )
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_higher_prob_horse_wins_more_often(self) -> None:
        """勝率の高い馬が1着になる確率が高い。"""
        wp = {1: 0.6, 2: 0.4}
        p1_wins = _harville_joint(wp, (1, 2))
        p2_wins = _harville_joint(wp, (2, 1))
        assert p1_wins > p2_wins

    def test_known_value(self) -> None:
        """手計算値との一致チェック。

        P(1着=A, 2着=B) = P(A) * P(B)/(1-P(A))
        = 0.4 * 0.3/(1-0.4) = 0.4 * 0.5 = 0.2
        """
        wp = {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}
        p = _harville_joint(wp, (1, 2))
        expected = 0.4 * (0.3 / (1 - 0.4))
        assert p == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# 3. combo_probability — 単勝
# ---------------------------------------------------------------------------


class TestTansho:
    """単勝確率テスト。"""

    def test_tansho_equals_win_prob(self) -> None:
        """単勝確率 = win_probability。"""
        p = combo_probability(WIN_PROBS_4, (1,), "tansho", "harville")
        assert p == pytest.approx(0.40, abs=1e-6)

    def test_all_tansho_sum_to_one(self) -> None:
        """全馬の単勝確率の和 = 1.0。"""
        total = sum(
            combo_probability(WIN_PROBS_4, (h,), "tansho", "harville")
            for h in WIN_PROBS_4
        )
        assert total == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 4. combo_probability — 馬連
# ---------------------------------------------------------------------------


class TestUmaren:
    """馬連確率テスト。"""

    def test_umaren_symmetric(self) -> None:
        """馬連は順不同: P(1,2) = P(2,1)。"""
        p12 = combo_probability(WIN_PROBS_4, (1, 2), "umaren", "harville")
        p21 = combo_probability(WIN_PROBS_4, (2, 1), "umaren", "harville")
        assert p12 == pytest.approx(p21, abs=1e-9)

    def test_all_umaren_sum_to_one(self) -> None:
        """全馬連組合せの確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "umaren", "harville")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_umaren_8horses_sum_to_one(self) -> None:
        """8頭での全馬連確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_8, "umaren", "harville")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_favorite_has_highest_umaren_prob(self) -> None:
        """最強馬(1番)が絡む馬連が最も確率が高い組合せ群を持つ。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "umaren", "harville")
        max_combo = max(combos, key=combos.__getitem__)
        assert 1 in max_combo  # 最強馬(horse_id=1) が最高確率組合せに入る

    def test_umaren_known_value(self) -> None:
        """手計算値との一致: P(1,2が1-2着) = P(1着=1,2着=2) + P(1着=2,2着=1)。

        P(A=1着, B=2着) = 0.4 * 0.3/0.6 = 0.2
        P(A=2着, B=1着) = 0.3 * 0.4/0.7 ≈ 0.1714...
        合計 ≈ 0.3714
        """
        wp = {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}
        p = combo_probability(wp, (1, 2), "umaren", "harville")
        p12 = 0.4 * (0.3 / 0.6)
        p21 = 0.3 * (0.4 / 0.7)
        assert p == pytest.approx(p12 + p21, abs=1e-6)


# ---------------------------------------------------------------------------
# 5. combo_probability — ワイド
# ---------------------------------------------------------------------------


class TestWide:
    """ワイド確率テスト。"""

    def test_wide_symmetric(self) -> None:
        """ワイドは順不同。"""
        p12 = combo_probability(WIN_PROBS_8, (1, 2), "wide", "harville")
        p21 = combo_probability(WIN_PROBS_8, (2, 1), "wide", "harville")
        assert p12 == pytest.approx(p21, abs=1e-9)

    def test_wide_ge_umaren(self) -> None:
        """ワイド確率 ≥ 馬連確率（3着以内は1-2着より広い条件）。"""
        for h1 in WIN_PROBS_8:
            for h2 in WIN_PROBS_8:
                if h1 >= h2:
                    continue
                p_wide = combo_probability(WIN_PROBS_8, (h1, h2), "wide", "harville")
                p_umaren = combo_probability(WIN_PROBS_8, (h1, h2), "umaren", "harville")
                assert p_wide >= p_umaren - 1e-9, f"h1={h1}, h2={h2}: wide={p_wide} < umaren={p_umaren}"

    def test_all_wide_sum_ge_one(self) -> None:
        """全ワイド確率の和 > 1.0（複数的中があるため）。"""
        combos = enumerate_combo_probs(WIN_PROBS_8, "wide", "harville")
        total = sum(combos.values())
        # 8頭では各レースで複数のワイドが的中するため合計 > 1
        assert total > 1.0


# ---------------------------------------------------------------------------
# 6. combo_probability — 三連複
# ---------------------------------------------------------------------------


class TestSanrenpuku:
    """三連複確率テスト。"""

    def test_all_sanrenpuku_sum_to_one(self) -> None:
        """全三連複確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "sanrenpuku", "harville")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_all_sanrenpuku_8horses_sum_to_one(self) -> None:
        """8頭での全三連複確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_8, "sanrenpuku", "harville")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_sanrenpuku_symmetric(self) -> None:
        """三連複は順不同: どの順番でも同じ確率。"""
        p123 = combo_probability(WIN_PROBS_4, (1, 2, 3), "sanrenpuku", "harville")
        p132 = combo_probability(WIN_PROBS_4, (1, 3, 2), "sanrenpuku", "harville")
        p312 = combo_probability(WIN_PROBS_4, (3, 1, 2), "sanrenpuku", "harville")
        assert p123 == pytest.approx(p132, abs=1e-9)
        assert p123 == pytest.approx(p312, abs=1e-9)


# ---------------------------------------------------------------------------
# 7. combo_probability — 三連単
# ---------------------------------------------------------------------------


class TestSanrentan:
    """三連単確率テスト。"""

    def test_all_sanrentan_sum_to_one(self) -> None:
        """全三連単確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "sanrentan", "harville")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_sanrentan_not_symmetric(self) -> None:
        """三連単は順序付: P(1,2,3) ≠ P(2,1,3)（一般的に）。"""
        p123 = combo_probability(WIN_PROBS_4, (1, 2, 3), "sanrentan", "harville")
        p213 = combo_probability(WIN_PROBS_4, (2, 1, 3), "sanrentan", "harville")
        # 勝率が異なれば確率は異なる
        assert p123 != pytest.approx(p213, abs=1e-6)

    def test_sanrentan_le_sanrenpuku(self) -> None:
        """三連単 ≤ 三連複（順序指定は条件がより厳しい）。"""
        p_fuku = combo_probability(WIN_PROBS_4, (1, 2, 3), "sanrenpuku", "harville")
        # 三連単 (1,2,3) + (1,3,2) + ... の合計 = 三連複
        import itertools
        p_tan_all = sum(
            combo_probability(WIN_PROBS_4, perm, "sanrentan", "harville")
            for perm in itertools.permutations((1, 2, 3))
        )
        assert p_tan_all == pytest.approx(p_fuku, abs=1e-6)

    def test_18horses_sanrentan_speed(self) -> None:
        """18頭三連単 4896点を 1秒以内で列挙できる。"""
        import time
        wp = {i + 1: 1.0 / 18 for i in range(18)}
        start = time.time()
        combos = enumerate_combo_probs(wp, "sanrentan", "harville")
        elapsed = time.time() - start
        assert len(combos) == 18 * 17 * 16  # 4896点
        assert elapsed < 1.0, f"列挙に {elapsed:.2f}秒かかった（1秒超）"

    def test_sanrentan_known_3horse(self) -> None:
        """3頭での手計算値との一致。

        P(1=1着, 2=2着, 3=3着)
          = P(1) * P(2)/(1-P(1)) * P(3)/(1-P(1)-P(2))
          = 0.5 * 0.3/0.5 * 0.2/0.2
          = 0.5 * 0.6 * 1.0
          = 0.3
        """
        wp = {1: 0.5, 2: 0.3, 3: 0.2}
        p = combo_probability(wp, (1, 2, 3), "sanrentan", "harville")
        expected = 0.5 * (0.3 / 0.5) * (0.2 / 0.2)
        assert p == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# 8. Henery λ=1.0 → Harville と同一
# ---------------------------------------------------------------------------


class TestHenery:
    """Henery モデルのテスト。"""

    def test_lambda_1_equals_harville(self) -> None:
        """λ=1.0 のとき Henery 調整確率 = 元の win_probs（Harville と同一になる）。"""
        wp = {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}
        adj = _henery_adjusted(wp, 1.0)
        for k in wp:
            assert adj[k] == pytest.approx(wp[k], abs=1e-9), f"horse {k}: {adj[k]} != {wp[k]}"

    def test_lambda_lt_1_boosts_underdogs(self) -> None:
        """λ<1.0 のとき低確率馬の調整確率が相対的に引き上げられる。"""
        wp = {1: 0.6, 2: 0.3, 3: 0.1}
        adj_1 = _henery_adjusted(wp, 1.0)
        adj_low = _henery_adjusted(wp, 0.5)
        # 1番（高確率）の比率は下がるはず
        ratio_1_at_1 = adj_1[1] / adj_1[3]
        ratio_1_at_05 = adj_low[1] / adj_low[3]
        assert ratio_1_at_05 < ratio_1_at_1

    def test_henery_umaren_sum_to_one(self) -> None:
        """Henery 手法でも全馬連確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "umaren", "henery")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_henery_sanrenpuku_sum_to_one(self) -> None:
        """Henery 手法でも全三連複確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "sanrenpuku", "henery")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_henery_sanrentan_sum_to_one(self) -> None:
        """Henery 手法でも全三連単確率の和 ≈ 1.0。"""
        combos = enumerate_combo_probs(WIN_PROBS_4, "sanrentan", "henery")
        total = sum(combos.values())
        assert total == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 9. composite.py の Harville 実装との一致確認
# ---------------------------------------------------------------------------


class TestCompositeCompatibility:
    """composite.py の _harville_place_probs との数値一致確認。"""

    def test_place_prob_matches_composite(self) -> None:
        """_harville_place_prob_single が composite.py と同じ値を返す。"""
        from src.indices.composite import CompositeIndexCalculator

        # composite.py はリスト形式で入力
        probs_list = [0.4, 0.3, 0.2, 0.1, 0.0]  # 5頭（8頭未満 → 2着以内）
        # 合計を 1.0 に正規化（0.0 を除外）
        probs_list = [0.4, 0.3, 0.2, 0.1]
        composite_place = CompositeIndexCalculator._harville_place_probs(probs_list)

        wp = {i + 1: p for i, p in enumerate(probs_list)}
        n = len(wp)
        place_within = 3 if n >= 8 else 2
        for i, (horse_id, p_composite) in enumerate(zip(wp.keys(), composite_place)):
            p_ours = _harville_place_prob_single(wp, horse_id, place_within)
            assert p_ours == pytest.approx(p_composite, abs=1e-9), (
                f"horse={horse_id}: ours={p_ours:.9f} composite={p_composite:.9f}"
            )

    def test_place_prob_8horses_matches_composite(self) -> None:
        """8頭（3着以内）での composite.py との一致確認。"""
        from src.indices.composite import CompositeIndexCalculator

        probs_list = [0.25, 0.20, 0.15, 0.15, 0.10, 0.07, 0.05, 0.03]
        composite_place = CompositeIndexCalculator._harville_place_probs(probs_list)

        wp = {i + 1: p for i, p in enumerate(probs_list)}
        n = len(wp)
        place_within = 3 if n >= 8 else 2
        for horse_id, p_composite in zip(wp.keys(), composite_place):
            p_ours = _harville_place_prob_single(wp, horse_id, place_within)
            assert p_ours == pytest.approx(p_composite, abs=1e-9), (
                f"horse={horse_id}: ours={p_ours:.9f} composite={p_composite:.9f}"
            )


# ---------------------------------------------------------------------------
# 10. エラー処理テスト
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """エッジケースのテスト。"""

    def test_empty_win_probs_returns_zero(self) -> None:
        """空の win_probs は 0 を返す。"""
        assert combo_probability({}, (1, 2), "umaren", "harville") == 0.0

    def test_single_horse_tansho(self) -> None:
        """1頭レースの単勝確率 = 1.0。"""
        p = combo_probability({1: 1.0}, (1,), "tansho", "harville")
        assert p == pytest.approx(1.0, abs=1e-9)

    def test_invalid_bet_type_raises(self) -> None:
        """未知の bet_type は ValueError を発生させる。"""
        with pytest.raises(ValueError, match="未知の bet_type"):
            combo_probability(WIN_PROBS_4, (1, 2), "invalid_bet", "harville")

    def test_invalid_method_raises(self) -> None:
        """未知の method は ValueError を発生させる（sanrentan での確認）。"""
        with pytest.raises(ValueError):
            combo_probability(WIN_PROBS_4, (1, 2, 3), "sanrentan", "unknown_method")
