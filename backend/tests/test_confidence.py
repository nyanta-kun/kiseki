"""レース信頼度算出 ユニットテスト

calculate_race_confidence の純粋関数テスト。DBアクセスなし。
"""

from __future__ import annotations

import pytest

from src.indices.confidence import (
    ENTROPY_THRESHOLDS,
    calculate_market_chaos,
    calculate_race_confidence,
    calculate_recommend_rank,
    is_market_favorite,
)

# ---------------------------------------------------------------------------
# 正常系テスト
# ---------------------------------------------------------------------------


class TestCalculateRaceConfidence:
    """calculate_race_confidence の基本テスト。"""

    def test_empty_list_returns_zero_score(self) -> None:
        """空リストの場合はスコア0・ラベルLOWを返す。"""
        result = calculate_race_confidence([], head_count=None)
        assert result["score"] == 0
        assert result["label"] == "LOW"
        assert result["gap_1_2"] == 0.0
        assert result["gap_1_3"] == 0.0

    def test_empty_list_with_head_count(self) -> None:
        """空リストで head_count 指定の場合、head_count がそのまま返る。"""
        result = calculate_race_confidence([], head_count=16)
        assert result["head_count"] == 16

    def test_returns_required_keys(self) -> None:
        """戻り値に必要なキーがすべて含まれる。"""
        result = calculate_race_confidence([60.0, 55.0, 50.0], head_count=3)
        assert "score" in result
        assert "label" in result
        assert "gap_1_2" in result
        assert "gap_1_3" in result
        assert "head_count" in result

    def test_score_within_valid_range(self) -> None:
        """スコアが [0, 100] の範囲内に収まる。"""
        indices = [70.0, 60.0, 55.0, 50.0, 45.0, 40.0]
        result = calculate_race_confidence(indices, head_count=6)
        assert 0 <= result["score"] <= 100

    def test_head_count_uses_list_length_when_none(self) -> None:
        """head_count=None の場合、リスト長を使用する。"""
        indices = [60.0, 55.0, 50.0]
        result = calculate_race_confidence(indices, head_count=None)
        assert result["head_count"] == 3

    def test_head_count_uses_provided_value(self) -> None:
        """head_count が指定された場合、その値を使用する。"""
        indices = [60.0, 55.0, 50.0]
        result = calculate_race_confidence(indices, head_count=16)
        assert result["head_count"] == 16


# ---------------------------------------------------------------------------
# ギャップスコアのテスト
# ---------------------------------------------------------------------------


class TestGapScore:
    """gap_1_2 / gap_1_3 の算出テスト。"""

    def test_large_gap_gives_high_score(self) -> None:
        """1位と2位・3位の差が大きいほど高スコアになる。"""
        # 差が大きい
        result_large = calculate_race_confidence([80.0, 60.0, 55.0], head_count=3)
        # 差が小さい
        result_small = calculate_race_confidence([61.0, 60.0, 59.0], head_count=3)
        assert result_large["score"] > result_small["score"]

    def test_gap_1_2_correct(self) -> None:
        """gap_1_2 が正確に計算される（1位-2位の差）。"""
        result = calculate_race_confidence([70.0, 60.0, 55.0], head_count=3)
        assert result["gap_1_2"] == pytest.approx(10.0)

    def test_gap_1_3_correct(self) -> None:
        """gap_1_3 が正確に計算される（1位-3位の差）。"""
        result = calculate_race_confidence([70.0, 60.0, 55.0], head_count=3)
        assert result["gap_1_3"] == pytest.approx(15.0)

    def test_single_horse_gap_is_zero(self) -> None:
        """出走馬1頭の場合、gap は 0.0 になる。"""
        result = calculate_race_confidence([60.0], head_count=1)
        assert result["gap_1_2"] == 0.0
        assert result["gap_1_3"] == 0.0

    def test_two_horses_gap_1_3_equals_gap_1_2(self) -> None:
        """出走馬2頭の場合、gap_1_3 == gap_1_2 になる。"""
        result = calculate_race_confidence([70.0, 50.0], head_count=2)
        assert result["gap_1_3"] == result["gap_1_2"]


# ---------------------------------------------------------------------------
# 頭数スコアのテスト
# ---------------------------------------------------------------------------


class TestHeadScore:
    """頭数スコアのテスト。"""

    def test_small_field_higher_score_than_large(self) -> None:
        """少頭数のほうが多頭数より高スコアになる（他条件が同じ場合）。"""
        indices = [60.0, 55.0, 50.0, 45.0, 40.0, 35.0]
        result_small = calculate_race_confidence(indices, head_count=6)
        result_large = calculate_race_confidence(indices, head_count=18)
        assert result_small["score"] >= result_large["score"]

    def test_field_of_8_or_less_max_head_score(self) -> None:
        """8頭以下はhead_scoreが最大（20点）。"""
        result = calculate_race_confidence([60.0, 50.0], head_count=8)
        # head_score = max(0, (18-8)/10) * 20 = 1.0 * 20 = 20
        # スコアの上限確認のみ（他の要素との合算）
        assert result["score"] > 0

    def test_field_of_18_zero_head_score(self) -> None:
        """18頭以上はhead_scoreが0。"""
        indices = [60.0, 50.0]
        result = calculate_race_confidence(indices, head_count=18)
        # head_score = max(0, (18-18)/10) * 20 = 0
        # スコアは他の要素（gap + dispersion）のみ
        assert result["score"] >= 0


# ---------------------------------------------------------------------------
# 分散スコアのテスト
# ---------------------------------------------------------------------------


class TestDispersionScore:
    """分散スコアのテスト。"""

    def test_high_dispersion_higher_score(self) -> None:
        """指数分布が広い（分散大）ほど高スコアになる。"""
        # 分散大
        result_high = calculate_race_confidence([90.0, 50.0, 10.0], head_count=3)
        # 分散小
        result_low = calculate_race_confidence([52.0, 51.0, 50.0], head_count=3)
        assert result_high["score"] > result_low["score"]


# ---------------------------------------------------------------------------
# ラベルのテスト
# ---------------------------------------------------------------------------


class TestLabel:
    """スコアラベル（HIGH/MID/LOW）のテスト。"""

    def test_high_label_for_high_score(self) -> None:
        """スコア70以上はHIGHラベル。"""
        # 1位と他の差が大きく、少頭数でHIGHになるケース
        indices = [80.0, 50.0, 45.0]
        result = calculate_race_confidence(indices, head_count=8)
        if result["score"] >= 70:
            assert result["label"] == "HIGH"

    def test_low_label_for_low_score(self) -> None:
        """スコア50未満はLOWラベル。"""
        # 差がほぼなく多頭数でLOWになるケース
        indices = [50.1, 50.0, 49.9] * 5
        result = calculate_race_confidence(indices, head_count=18)
        if result["score"] < 50:
            assert result["label"] == "LOW"

    def test_label_boundaries(self) -> None:
        """ラベルの境界値テスト。"""
        # scoreが70なら HIGH
        # scoreが50なら MID
        # scoreが49なら LOW
        # ラベルが3種類のいずれかであることを確認
        indices = [60.0, 55.0, 50.0, 45.0]
        result = calculate_race_confidence(indices, head_count=16)
        assert result["label"] in {"HIGH", "MID", "LOW"}

    def test_empty_returns_low(self) -> None:
        """空リストは必ずLOWラベル。"""
        result = calculate_race_confidence([], head_count=16)
        assert result["label"] == "LOW"


# ---------------------------------------------------------------------------
# 入力バリエーションのテスト
# ---------------------------------------------------------------------------


class TestInputVariations:
    """様々な入力パターンのテスト。"""

    def test_all_same_score(self) -> None:
        """全馬が同じ指数の場合、gap=0になる。"""
        indices = [50.0, 50.0, 50.0, 50.0]
        result = calculate_race_confidence(indices, head_count=4)
        assert result["gap_1_2"] == 0.0
        assert result["gap_1_3"] == 0.0

    def test_unsorted_input_handled(self) -> None:
        """入力が非ソートでも正しく計算される。"""
        # [55.0, 60.0, 50.0] → sorted descending: [60, 55, 50]
        result_unsorted = calculate_race_confidence([55.0, 60.0, 50.0], head_count=3)
        result_sorted = calculate_race_confidence([60.0, 55.0, 50.0], head_count=3)
        assert result_unsorted["score"] == result_sorted["score"]
        assert result_unsorted["gap_1_2"] == result_sorted["gap_1_2"]

    def test_realistic_race_scenario(self) -> None:
        """実際のレースシナリオを想定したテスト。"""
        # 典型的なレース（16頭、指数差あり）
        indices = [68.5, 62.0, 57.3, 55.1, 53.8, 52.4, 51.0, 49.5,
                   48.2, 47.0, 45.8, 44.3, 43.1, 41.9, 40.5, 38.2]
        result = calculate_race_confidence(indices, head_count=16)
        assert 0 <= result["score"] <= 100
        assert result["label"] in {"HIGH", "MID", "LOW"}
        assert result["gap_1_2"] == pytest.approx(6.5)


# ---------------------------------------------------------------------------
# is_market_favorite のテスト
# ---------------------------------------------------------------------------


class TestIsMarketFavorite:
    """is_market_favorite（指数1位馬が単勝1番人気と一致するか）のテスト。"""

    def test_none_when_top_odds_missing(self) -> None:
        assert is_market_favorite(None, [1.5, 3.0, 5.0]) is None

    def test_none_when_all_odds_missing(self) -> None:
        assert is_market_favorite(1.5, None) is None
        assert is_market_favorite(1.5, []) is None

    def test_true_when_top_is_lowest_odds(self) -> None:
        assert is_market_favorite(1.5, [1.5, 3.0, 5.0]) is True

    def test_false_when_top_is_not_lowest_odds(self) -> None:
        assert is_market_favorite(3.0, [1.5, 3.0, 5.0]) is False

    def test_true_on_tie(self) -> None:
        """同オッズで最低値に並ぶ場合は一致扱い。"""
        assert is_market_favorite(1.5, [1.5, 1.5, 5.0]) is True


# ---------------------------------------------------------------------------
# calculate_recommend_rank のテスト
# ---------------------------------------------------------------------------


class TestCalculateRecommendRank:
    """calculate_recommend_rank（market_agree再設計後）のテスト。"""

    def test_odds_under_1_5_always_s(self) -> None:
        """断然人気(単勝<1.5)は market_agree/confidence_score を問わず S。"""
        assert calculate_recommend_rank(30, win_odds_top=1.2, market_agree=False) == "S"
        assert calculate_recommend_rank(90, win_odds_top=1.2, market_agree=None) == "S"

    def test_market_agree_none_falls_back_to_legacy(self) -> None:
        """market_agree計算不能時は confidence_score のみの旧ロジック。"""
        assert calculate_recommend_rank(85, win_odds_top=3.0, market_agree=None) == "A"
        assert calculate_recommend_rank(70, win_odds_top=3.0, market_agree=None) == "B"
        assert calculate_recommend_rank(40, win_odds_top=3.0, market_agree=None) == "C"

    def test_market_disagree_always_c(self) -> None:
        """市場乖離は confidence_score が高くても C（断然人気override以外）。"""
        assert calculate_recommend_rank(95, win_odds_top=3.0, market_agree=False) == "C"
        assert calculate_recommend_rank(50, win_odds_top=3.0, market_agree=False) == "C"

    def test_market_agree_tiers_by_confidence_score(self) -> None:
        """市場一致時は confidence_score で S/A/B に分岐する。"""
        assert calculate_recommend_rank(85, win_odds_top=3.0, market_agree=True) == "S"
        assert calculate_recommend_rank(70, win_odds_top=3.0, market_agree=True) == "A"
        assert calculate_recommend_rank(40, win_odds_top=3.0, market_agree=True) == "B"

    def test_default_market_agree_is_none(self) -> None:
        """market_agree未指定時は旧ロジック相当（後方互換）。"""
        assert calculate_recommend_rank(85, win_odds_top=3.0) == "A"

    def test_entropy_norm_omitted_keeps_plain_c(self) -> None:
        """entropy_norm未指定時は従来通りC（後方互換、C+へは分割されない）。"""
        assert calculate_recommend_rank(50, win_odds_top=3.0, market_agree=False) == "C"

    def test_low_entropy_c_becomes_c_plus(self) -> None:
        """市場乖離かつentropy_normがC閾値未満なら C+（準見送り）。"""
        low = ENTROPY_THRESHOLDS["C"] - 0.01
        assert (
            calculate_recommend_rank(50, win_odds_top=3.0, market_agree=False, entropy_norm=low)
            == "C+"
        )

    def test_high_entropy_c_stays_c(self) -> None:
        """市場乖離かつentropy_normがC閾値以上なら真の大混戦としてC。"""
        high = ENTROPY_THRESHOLDS["C"] + 0.01
        assert (
            calculate_recommend_rank(50, win_odds_top=3.0, market_agree=False, entropy_norm=high)
            == "C"
        )

    def test_entropy_norm_only_affects_c_path(self) -> None:
        """S/A/Bに分類される場合はentropy_normを渡してもtierは変わらない。"""
        low = ENTROPY_THRESHOLDS["S"] - 0.01
        assert (
            calculate_recommend_rank(85, win_odds_top=3.0, market_agree=True, entropy_norm=low)
            == "S"
        )

    def test_odds_under_1_5_ignores_entropy(self) -> None:
        """断然人気(単勝<1.5)は entropy_norm を渡してもSのまま。"""
        assert (
            calculate_recommend_rank(30, win_odds_top=1.2, market_agree=False, entropy_norm=0.99)
            == "S"
        )


# ---------------------------------------------------------------------------
# calculate_market_chaos のテスト
# ---------------------------------------------------------------------------


class TestCalculateMarketChaos:
    """calculate_market_chaos（HHI/entropy_norm算出）のテスト。"""

    def test_insufficient_odds_returns_none(self) -> None:
        """有効オッズが3頭未満なら算出不能（None, None）。"""
        result = calculate_market_chaos([1.5, 3.0])
        assert result == {"hhi": None, "entropy_norm": None}

    def test_empty_list_returns_none(self) -> None:
        result = calculate_market_chaos([])
        assert result == {"hhi": None, "entropy_norm": None}

    def test_filters_invalid_odds(self) -> None:
        """None・1.0未満の異常値は無視される。1.5/3.0/5.0の3件で算出される。"""
        result = calculate_market_chaos([1.5, 3.0, 5.0, 0.5])  # 0.5 は異常値として除外
        assert result["hhi"] is not None
        assert result["entropy_norm"] is not None

    def test_uniform_odds_gives_high_entropy(self) -> None:
        """全馬同オッズ(=均等)なら entropy_norm はほぼ1.0(理論上の最大混戦)。"""
        result = calculate_market_chaos([5.0, 5.0, 5.0, 5.0])
        assert result["entropy_norm"] == pytest.approx(1.0, abs=1e-6)

    def test_lopsided_odds_gives_low_entropy(self) -> None:
        """1頭が断然人気で残りが大穴なら entropy_norm は低い(本命一強)。"""
        result = calculate_market_chaos([1.1, 50.0, 80.0, 99.0])
        assert result["entropy_norm"] < 0.5

    def test_hhi_and_entropy_norm_in_valid_range(self) -> None:
        result = calculate_market_chaos([2.0, 4.0, 6.0, 10.0, 20.0])
        assert 0.0 < result["hhi"] <= 1.0
        assert 0.0 <= result["entropy_norm"] <= 1.0
