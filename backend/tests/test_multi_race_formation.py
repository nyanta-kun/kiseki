"""multi_race_formation.py のユニットテスト。

DoD 要件:
- 厳密 DP と限界効用貪欲の解が一致する（複数の分布パターンで）
- 「上位が一定以上の割合を持つレースは 1 点のまま残る」ことを確認
- 予算（円）→ 点数上限の換算、固定制約つき再最適化、検証用メトリクスの整合
- 予算の両端（極小・極大）での挙動
- 同じ点数以下で「一律 5 頭」より的中率が上がることを実データ相当の分布で確認

DB・モデルに一切依存しない純関数のテスト。
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from src.betting.multi_race_formation import (
    DEFAULT_UNIT_PRICE,
    FormationPlan,
    RaceCandidates,
    _marginal_utility,
    _prepare_race,
    _solve_exact,
    _solve_greedy,
    budget_to_max_tickets,
    evaluate_formation,
    optimize_formation,
)

# ---------------------------------------------------------------------------
# テスト用の勝率分布
# ---------------------------------------------------------------------------


def _normalized(weights: list[float]) -> list[float]:
    """重み列をレース内正規化した勝率列（降順）にする。"""
    total = sum(weights)
    return sorted((w / total for w in weights), reverse=True)


# 断然人気（1 番人気が半分以上を持つ）。1 点で止めたいレース。
DOMINANT = _normalized([55.0, 12.0, 9.0, 7.0, 5.0, 4.0, 3.0, 2.0, 2.0, 1.0])
# 平坦（混戦）。広げる価値が大きいレース。
FLAT = _normalized([9.0, 8.5, 8.0, 8.0, 7.5, 7.5, 7.0, 7.0, 6.5, 6.5, 6.0, 6.0, 6.0, 6.5])
# 平坦だが FLAT とは別形（同点解によるテストの曖昧さを避けるため）。
FLAT_ALT = _normalized([10.0, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0, 4.5, 4.0])
# 中間（前 3 頭が厚め）。
MIDDLE = _normalized([28.0, 18.0, 14.0, 10.0, 8.0, 6.0, 5.0, 4.0, 4.0, 3.0])
# 少頭数（8 頭立て）。
SMALL_FIELD = _normalized([30.0, 20.0, 15.0, 12.0, 9.0, 6.0, 5.0, 3.0])


def _win5(probs_list: list[list[float]]) -> list[RaceCandidates]:
    """勝率列のリストから WIN5 相当の 5 レース入力を作る。"""
    return [
        RaceCandidates(
            race_id=f"R{i + 1}",
            win_probs=probs,
            horses=list(range(1, len(probs) + 1)),
        )
        for i, probs in enumerate(probs_list)
    ]


# ---------------------------------------------------------------------------
# budget_to_max_tickets
# ---------------------------------------------------------------------------


class TestBudgetToMaxTickets:
    """予算（円）→ 点数上限の換算テスト。"""

    def test_win5_unit_price(self) -> None:
        """WIN5 は 1 点 100 円。100,000 円 -> 1,000 点。"""
        assert budget_to_max_tickets(100_000) == 1_000
        assert budget_to_max_tickets(100_000, DEFAULT_UNIT_PRICE) == 1_000

    def test_truncates_remainder(self) -> None:
        """端数は切り捨てる。"""
        assert budget_to_max_tickets(10_050) == 100

    def test_zero_budget(self) -> None:
        """0 円なら 0 点。"""
        assert budget_to_max_tickets(0) == 0

    def test_custom_unit_price(self) -> None:
        """地方重勝式など単価が異なる場合にも対応する。"""
        assert budget_to_max_tickets(6_000, unit_price=200) == 30

    def test_rejects_negative_budget(self) -> None:
        """負の予算は ValueError。"""
        with pytest.raises(ValueError, match="budget_yen"):
            budget_to_max_tickets(-100)

    def test_rejects_zero_unit_price(self) -> None:
        """単価 0 は ValueError。"""
        with pytest.raises(ValueError, match="unit_price"):
            budget_to_max_tickets(1_000, unit_price=0)


# ---------------------------------------------------------------------------
# _prepare_race（前処理）
# ---------------------------------------------------------------------------


class TestPrepareRace:
    """入力の検証・整列・カバレッジ化のテスト。"""

    def test_cumulative_coverage(self) -> None:
        """カバレッジは勝率の累積和になる。"""
        race = RaceCandidates(race_id="R1", win_probs=[0.5, 0.3, 0.2])
        prepared = _prepare_race(race)
        assert prepared.coverage == pytest.approx((0.5, 0.8, 1.0))

    def test_drops_zero_probability_horses(self) -> None:
        """p=0 の馬は log(0) を踏まないよう除外される。"""
        race = RaceCandidates(race_id="R1", win_probs=[0.5, 0.3, 0.2, 0.0, 0.0])
        prepared = _prepare_race(race)
        assert len(prepared.coverage) == 3
        assert all(c > 0.0 for c in prepared.coverage)

    def test_sorts_descending_with_horse_numbers(self) -> None:
        """降順でない入力は並べ替えられ、馬番の対応関係も保たれる。"""
        race = RaceCandidates(race_id="R1", win_probs=[0.2, 0.5, 0.3], horses=[7, 8, 9])
        prepared = _prepare_race(race)
        assert prepared.horses == (8, 9, 7)
        assert prepared.coverage == pytest.approx((0.5, 0.8, 1.0))

    def test_renormalizes_unnormalized_input(self) -> None:
        """合計が 1.0 でない入力は安全弁として再正規化される。"""
        race = RaceCandidates(race_id="R1", win_probs=[5.0, 3.0, 2.0])
        prepared = _prepare_race(race)
        assert prepared.coverage[-1] == pytest.approx(1.0)

    def test_max_picks_caps_candidates(self) -> None:
        """max_picks（出走頭数など）で候補が打ち切られる。"""
        race = RaceCandidates(race_id="R1", win_probs=[0.4, 0.3, 0.2, 0.1], max_picks=2)
        prepared = _prepare_race(race)
        assert len(prepared.coverage) == 2

    def test_rejects_all_zero(self) -> None:
        """有効な勝率が 1 頭もなければ ValueError。"""
        with pytest.raises(ValueError, match="有効な勝率"):
            _prepare_race(RaceCandidates(race_id="R1", win_probs=[0.0, 0.0]))

    def test_rejects_horses_length_mismatch(self) -> None:
        """horses の長さ不一致は ValueError。"""
        with pytest.raises(ValueError, match="horses"):
            _prepare_race(RaceCandidates(race_id="R1", win_probs=[0.5, 0.5], horses=[1]))

    def test_rejects_out_of_range_fixed_picks(self) -> None:
        """fixed_picks が候補頭数を超えたら ValueError。"""
        with pytest.raises(ValueError, match="fixed_picks"):
            _prepare_race(RaceCandidates(race_id="R1", win_probs=[0.5, 0.5], fixed_picks=5))


# ---------------------------------------------------------------------------
# _marginal_utility
# ---------------------------------------------------------------------------


class TestMarginalUtility:
    """限界効用 Δlog cov / Δlog k のテスト。"""

    def test_hand_calculated_value(self) -> None:
        """手計算例との一致。

        cov = (0.5, 0.8) で k=1 -> 2:
        (log 0.8 - log 0.5) / (log 2 - log 1) = log(1.6) / log(2) = 0.678...
        """
        result = _marginal_utility((0.5, 0.8), 1, 2)
        assert result == pytest.approx(math.log(1.6) / math.log(2.0))

    def test_zero_when_not_expanding(self) -> None:
        """広げないなら限界効用 0。"""
        assert _marginal_utility((0.5, 0.8), 2, 2) == 0.0

    def test_dominant_favourite_has_low_utility(self) -> None:
        """断然人気のレースは 2 頭目の限界効用が小さい。"""
        dominant = _prepare_race(RaceCandidates(race_id="D", win_probs=DOMINANT))
        flat = _prepare_race(RaceCandidates(race_id="F", win_probs=FLAT))
        assert _marginal_utility(dominant.coverage, 1, 2) < _marginal_utility(flat.coverage, 1, 2)


# ---------------------------------------------------------------------------
# 厳密解と貪欲解の一致
# ---------------------------------------------------------------------------


class TestExactMatchesGreedy:
    """厳密 DP と限界効用貪欲が同じ解に到達することを確認する。"""

    @pytest.mark.parametrize(
        "max_tickets",
        [1, 2, 4, 8, 16, 32, 60, 100, 243, 500, 1_000, 3_125, 10_000, 100_000],
    )
    def test_win5_mixed_distributions(self, max_tickets: int) -> None:
        """混在分布の WIN5 で、幅広い予算に対して厳密解と貪欲解が一致する。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, SMALL_FIELD, FLAT_ALT])
        exact = optimize_formation(races, max_tickets=max_tickets, method="exact")
        greedy = optimize_formation(races, max_tickets=max_tickets, method="greedy")
        assert [a.picks for a in greedy.allocations] == [a.picks for a in exact.allocations]
        assert greedy.hit_probability == pytest.approx(exact.hit_probability)

    @pytest.mark.parametrize("max_tickets", [1, 5, 12, 30, 81, 200, 729])
    def test_chihou_three_race_exacta(self, max_tickets: int) -> None:
        """地方重勝式（3レース）想定でも一致する。"""
        races = [
            RaceCandidates(race_id="K1", win_probs=DOMINANT),
            RaceCandidates(race_id="K2", win_probs=SMALL_FIELD),
            RaceCandidates(race_id="K3", win_probs=MIDDLE),
        ]
        exact = optimize_formation(races, max_tickets=max_tickets, method="exact")
        greedy = optimize_formation(races, max_tickets=max_tickets, method="greedy")
        assert [a.picks for a in greedy.allocations] == [a.picks for a in exact.allocations]

    def test_randomised_distributions(self) -> None:
        """乱数生成した極端な分布での一致率と最悪劣化を固定する。

        貪欲は整数制約（点数が k の積で効く）由来のギャップを原理的には消せないため、
        100% 一致は保証できない。ここでは「ほぼ一致し、外しても厳密解に肉薄する」ことを
        回帰テストとして固定する。実務レンジの分布では上の parametrize が完全一致を要求している。
        """
        import random

        rng = random.Random(20260902)
        matched = 0
        trials = 0
        worst_ratio = 1.0
        for _ in range(120):
            n_races = rng.randint(2, 5)
            races = []
            for i in range(n_races):
                size = rng.randint(3, 16)
                weights = [rng.random() ** rng.uniform(0.5, 3.0) + 0.01 for _ in range(size)]
                races.append(RaceCandidates(race_id=f"R{i}", win_probs=_normalized(weights)))
            budget = rng.choice([4, 10, 50, 200, 1_000, 5_000])
            exact = optimize_formation(races, max_tickets=budget, method="exact")
            greedy = optimize_formation(races, max_tickets=budget, method="greedy")
            assert greedy.total_tickets <= budget
            assert exact.total_tickets <= budget
            # 厳密解が貪欲解を下回ることは決してない
            assert exact.hit_probability >= greedy.hit_probability - 1e-12
            trials += 1
            ratio = greedy.hit_probability / exact.hit_probability
            worst_ratio = min(worst_ratio, ratio)
            if ratio > 1.0 - 1e-9:
                matched += 1
        assert matched / trials >= 0.95, f"一致率が低い: {matched}/{trials}"
        assert worst_ratio >= 0.95, f"貪欲の最悪劣化が大きい: {worst_ratio:.4f}"

    def test_solvers_called_directly(self) -> None:
        """ソルバを直接呼んでも同じ頭数ベクトルを返す。"""
        prepared = [_prepare_race(r) for r in _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])]
        assert _solve_exact(prepared, 1_000) == _solve_greedy(prepared, 1_000)


# ---------------------------------------------------------------------------
# 「上位が偏るレースは 1 点になる」
# ---------------------------------------------------------------------------


class TestDominantRaceStaysSingle:
    """ユーザー要件 1: 上位が一定以上の割合を持つレースは 1 点に残る。"""

    def test_dominant_race_kept_at_one_pick(self) -> None:
        """断然人気（1 番人気 55%）のレースだけ 1 点のまま、他レースが広がる。"""
        races = _win5([DOMINANT, FLAT, FLAT, FLAT, FLAT])
        plan = optimize_formation(races, budget_yen=100_000)  # 1,000 点
        picks = {a.race_id: a.picks for a in plan.allocations}
        assert picks["R1"] == 1, f"断然人気レースが広がってしまった: {picks}"
        assert all(picks[f"R{i}"] > 1 for i in range(2, 6)), f"混戦レースが広がっていない: {picks}"

    @pytest.mark.parametrize("max_tickets", [8, 32, 128, 512, 2_048])
    def test_dominant_race_last_to_expand(self, max_tickets: int) -> None:
        """予算を変えても、断然人気レースは常に最も狭い（= 最後に広げる対象）。"""
        races = _win5([DOMINANT, FLAT, FLAT, MIDDLE, MIDDLE])
        plan = optimize_formation(races, max_tickets=max_tickets)
        picks = [a.picks for a in plan.allocations]
        assert picks[0] == min(picks), f"断然人気レースが最も狭くない: {picks}"

    def test_threshold_behaviour_of_top_share(self) -> None:
        """1 番人気の比率が上がるほど、その馬に配分される頭数は単調に減る。"""
        picks_by_share: list[int] = []
        for top in (0.10, 0.20, 0.30, 0.45, 0.60, 0.80):
            rest = [(1.0 - top) / 9.0] * 9
            races = _win5([[top, *rest], FLAT, FLAT, MIDDLE, MIDDLE])
            plan = optimize_formation(races, max_tickets=1_000)
            picks_by_share.append(plan.allocations[0].picks)
        assert picks_by_share == sorted(picks_by_share, reverse=True), picks_by_share
        assert picks_by_share[-1] == 1, f"1 番人気 80% でも 1 点にならない: {picks_by_share}"

    def test_all_dominant_races_stay_at_one_when_budget_is_thin(self) -> None:
        """全レースが断然人気なら、そこそこの予算でも全て 1 点に近い狭さで止まる。"""
        races = _win5([DOMINANT] * 5)
        plan = optimize_formation(races, max_tickets=4)
        assert plan.total_tickets <= 4
        # 限界効用が低いので予算 4 点を使い切らずに 1 点で止まるレースが残る
        assert sum(1 for a in plan.allocations if a.picks == 1) >= 3


# ---------------------------------------------------------------------------
# 予算の両端
# ---------------------------------------------------------------------------


class TestBudgetExtremes:
    """予算が極小・極大のときの挙動。"""

    def test_budget_below_one_ticket(self) -> None:
        """1 点も買えない予算でも、最小構成（全レース 1 点）を返す。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        plan = optimize_formation(races, budget_yen=50)  # 0 点
        assert [a.picks for a in plan.allocations] == [1, 1, 1, 1, 1]
        assert plan.total_tickets == 1
        assert plan.total_cost == 100

    def test_exactly_one_ticket(self) -> None:
        """1 点ちょうどなら全レース 1 点。的中確率は各レース 1 番人気の積。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        plan = optimize_formation(races, max_tickets=1)
        expected = math.prod(sorted(p, reverse=True)[0] for p in [DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        assert plan.hit_probability == pytest.approx(expected)
        assert plan.next_expansion is not None
        assert plan.next_expansion.additional_tickets == 1

    def test_huge_budget_buys_every_horse(self) -> None:
        """予算が極大なら全頭買いで頭打ちになり、的中確率は 1.0。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        plan = optimize_formation(races, budget_yen=10_000_000_000)
        assert [a.picks for a in plan.allocations] == [
            len(DOMINANT),
            len(FLAT),
            len(MIDDLE),
            len(FLAT),
            len(SMALL_FIELD),
        ]
        assert plan.hit_probability == pytest.approx(1.0)
        assert plan.next_expansion is None  # これ以上広げられない

    def test_no_budget_specified_means_full_coverage(self) -> None:
        """予算未指定なら制約なし = 全頭買い。"""
        races = _win5([DOMINANT, SMALL_FIELD, SMALL_FIELD, SMALL_FIELD, SMALL_FIELD])
        plan = optimize_formation(races)
        assert plan.hit_probability == pytest.approx(1.0)
        assert plan.within_budget

    def test_max_picks_limits_expansion(self) -> None:
        """出走頭数（max_picks）を超えて広がらない。"""
        races = [
            RaceCandidates(race_id="R1", win_probs=FLAT, max_picks=3),
            RaceCandidates(race_id="R2", win_probs=FLAT, max_picks=2),
        ]
        plan = optimize_formation(races, max_tickets=10_000)
        assert [a.picks for a in plan.allocations] == [3, 2]
        assert plan.total_tickets == 6


# ---------------------------------------------------------------------------
# 固定制約つき再最適化
# ---------------------------------------------------------------------------


class TestFixedPicks:
    """ユーザー要件 3: 買い目を変更したうえで残り予算を再最適化する。"""

    def test_fixed_race_is_honoured(self) -> None:
        """「レース1（断然人気）は必ず 2 頭」を守り、残り予算で再最適化する。"""
        races = _win5([DOMINANT, FLAT, FLAT, MIDDLE, MIDDLE])
        base = optimize_formation(races, max_tickets=1_000)
        assert base.allocations[0].picks == 1  # 放っておけば 1 点

        modified = [replace(races[0], fixed_picks=2), *races[1:]]
        plan = optimize_formation(modified, max_tickets=1_000)
        assert plan.allocations[0].picks == 2
        assert plan.allocations[0].fixed is True
        assert plan.total_tickets <= 1_000

    def test_fixed_race_reduces_others(self) -> None:
        """固定で点数を使った分、他レースの広さは同じか狭くなる。"""
        races = _win5([DOMINANT, FLAT, FLAT, MIDDLE, MIDDLE])
        base = optimize_formation(races, max_tickets=1_000)
        modified = [replace(races[0], fixed_picks=4), *races[1:]]
        plan = optimize_formation(modified, max_tickets=1_000)
        for a_base, a_new in zip(base.allocations[1:], plan.allocations[1:], strict=True):
            assert a_new.picks <= a_base.picks
        # 固定によって的中確率は base 以下になる（base が最適解なので）
        assert plan.hit_probability <= base.hit_probability + 1e-12

    def test_fixed_race_excluded_from_next_expansion(self) -> None:
        """固定レースは「次に広げる推奨」の対象にならない。"""
        races = _win5([FLAT, FLAT, FLAT, FLAT, FLAT])
        modified = [replace(races[0], fixed_picks=1), *races[1:]]
        plan = optimize_formation(modified, max_tickets=100)
        assert plan.next_expansion is not None
        assert plan.next_expansion.race_id != "R1"

    def test_fixed_picks_can_exceed_budget(self) -> None:
        """固定だけで予算超過でも解は返し、within_budget=False で報告する。"""
        races = [
            RaceCandidates(race_id="R1", win_probs=FLAT, fixed_picks=6),
            RaceCandidates(race_id="R2", win_probs=FLAT, fixed_picks=6),
        ]
        plan = optimize_formation(races, max_tickets=10)
        assert [a.picks for a in plan.allocations] == [6, 6]
        assert plan.total_tickets == 36
        assert plan.within_budget is False

    def test_all_fixed_matches_evaluate(self) -> None:
        """全レース固定なら、その頭数の evaluate_formation と一致する。"""
        picks = [2, 3, 1, 4, 2]
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        fixed = [replace(r, fixed_picks=k) for r, k in zip(races, picks, strict=True)]
        plan = optimize_formation(fixed, max_tickets=10_000)
        manual = evaluate_formation(races, picks)
        assert [a.picks for a in plan.allocations] == picks
        assert plan.hit_probability == pytest.approx(manual.hit_probability)


# ---------------------------------------------------------------------------
# 検証用メトリクス
# ---------------------------------------------------------------------------


class TestPlanMetrics:
    """ユーザー要件 4: 返り値だけで買い目の検証ができる。"""

    def test_metrics_are_self_consistent(self) -> None:
        """総点数・総額・的中確率が各レースの値と整合する。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        plan = optimize_formation(races, budget_yen=312_500)
        assert plan.total_tickets == math.prod(a.picks for a in plan.allocations)
        assert plan.total_cost == plan.total_tickets * plan.unit_price
        assert plan.hit_probability == pytest.approx(math.prod(a.coverage for a in plan.allocations))
        assert plan.total_tickets <= plan.max_tickets
        assert plan.within_budget

    def test_selected_horses_are_top_ranked(self) -> None:
        """selected_horses は勝率上位から k 頭。"""
        race = RaceCandidates(race_id="R1", win_probs=[0.1, 0.5, 0.4], horses=[11, 12, 13])
        plan = evaluate_formation([race], [2])
        assert plan.allocations[0].selected_horses == (12, 13)
        assert plan.allocations[0].coverage == pytest.approx(0.9)

    def test_next_expansion_points_at_best_marginal_utility(self) -> None:
        """next_expansion は限界効用が最大のレースを指す。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        plan = optimize_formation(races, max_tickets=100)
        expansion = plan.next_expansion
        assert expansion is not None
        utilities = [
            a.marginal_utility for a in plan.allocations if a.marginal_utility is not None
        ]
        assert expansion.marginal_utility == pytest.approx(max(utilities))
        # 実際に広げた結果と一致する
        picks = [
            a.picks + 1 if a.race_id == expansion.race_id else a.picks for a in plan.allocations
        ]
        widened = evaluate_formation(races, picks)
        assert widened.hit_probability == pytest.approx(expansion.hit_probability_after)
        assert widened.total_tickets - plan.total_tickets == expansion.additional_tickets
        assert expansion.additional_cost == expansion.additional_tickets * DEFAULT_UNIT_PRICE

    def test_evaluate_formation_rejects_bad_input(self) -> None:
        """evaluate_formation は長さ不一致・範囲外を弾く。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        with pytest.raises(ValueError, match="長さが一致しません"):
            evaluate_formation(races, [1, 1, 1])
        with pytest.raises(ValueError, match="範囲外"):
            evaluate_formation(races, [1, 1, 1, 1, 99])

    def test_rejects_invalid_method_and_empty_races(self) -> None:
        """method 不正・races 空は ValueError。"""
        races = _win5([DOMINANT, FLAT, MIDDLE, FLAT, SMALL_FIELD])
        with pytest.raises(ValueError, match="method"):
            optimize_formation(races, max_tickets=10, method="dp")
        with pytest.raises(ValueError, match="races"):
            optimize_formation([], max_tickets=10)


# ---------------------------------------------------------------------------
# 「一律 5 頭」との比較（実測ベースの分布）
# ---------------------------------------------------------------------------


# JRA 前向き記録 217 レースの実測: 勝ち馬が指数上位 k 頭に含まれる率
# k=1:27.2% / 2:44.7% / 3:56.2% / 4:65.9% / 5:75.1% / 6:83.4%
OBSERVED_TOPK_COVERAGE = [0.272, 0.447, 0.562, 0.659, 0.751, 0.834]


def _coverage_to_probs(coverage: list[float], tail: int) -> list[float]:
    """累積カバレッジ列から勝率列を復元し、残りを均等な裾で埋める。"""
    probs = [coverage[0]]
    probs += [coverage[i] - coverage[i - 1] for i in range(1, len(coverage))]
    remaining = 1.0 - coverage[-1]
    probs += [remaining / tail] * tail
    return probs


class TestBeatsUniformFive:
    """同じ点数以下で「一律 5 頭」を上回ることを確認する。"""

    def test_average_distribution_matches_observed_coverage(self) -> None:
        """平均像（実測カバレッジそのもの）を 5 レース使うと一律 5 頭 = 23.9% を再現する。"""
        avg = _coverage_to_probs(OBSERVED_TOPK_COVERAGE, tail=6)
        races = _win5([avg] * 5)
        uniform = evaluate_formation(races, [5] * 5)
        assert uniform.total_tickets == 3_125
        assert uniform.total_cost == 312_500
        assert uniform.hit_probability == pytest.approx(0.751**5, rel=1e-9)
        assert uniform.hit_probability == pytest.approx(0.239, abs=0.001)

    def test_optimizer_beats_uniform_five_within_same_budget(self) -> None:
        """レースごとに分布が違うとき、3,125 点以下で一律 5 頭より的中率が上がる。

        実測の平均カバレッジ（k=5 で 75.1%）を保ちつつ、レースごとに
        「上位に偏った回」「混戦の回」がある想定の分布を作る。
        """
        skewed = _normalized([46.0, 14.0, 9.0, 6.0, 5.0, 4.0, 4.0, 3.0, 3.0, 3.0, 2.0, 1.0])
        contested = _normalized([15.0, 13.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0])
        average = _coverage_to_probs(OBSERVED_TOPK_COVERAGE, tail=6)
        races = _win5([skewed, contested, average, contested, skewed])

        uniform = evaluate_formation(races, [5] * 5)
        optimized = optimize_formation(races, max_tickets=uniform.total_tickets)

        assert optimized.total_tickets <= uniform.total_tickets
        assert optimized.hit_probability > uniform.hit_probability, (
            f"uniform={uniform.hit_probability:.4f} optimized={optimized.hit_probability:.4f} "
            f"picks={[a.picks for a in optimized.allocations]}"
        )
        # 偏ったレースは 5 頭より狭く、混戦レースは 5 頭より広くなる
        picks = [a.picks for a in optimized.allocations]
        assert picks[0] < 5 and picks[4] < 5, picks
        assert picks[1] > 5 and picks[3] > 5, picks

    def test_same_hit_rate_for_fewer_tickets(self) -> None:
        """一律 5 頭と同等以上の的中率を、より少ない点数で達成できる。"""
        skewed = _normalized([46.0, 14.0, 9.0, 6.0, 5.0, 4.0, 4.0, 3.0, 3.0, 3.0, 2.0, 1.0])
        contested = _normalized([15.0, 13.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0])
        average = _coverage_to_probs(OBSERVED_TOPK_COVERAGE, tail=6)
        races = _win5([skewed, contested, average, contested, skewed])
        uniform = evaluate_formation(races, [5] * 5)

        cheaper: FormationPlan | None = None
        for budget in (1_000, 1_500, 2_000, 2_500):
            plan = optimize_formation(races, max_tickets=budget)
            if plan.hit_probability >= uniform.hit_probability:
                cheaper = plan
                break
        assert cheaper is not None, "一律 5 頭より少ない点数で同等の的中率に届かなかった"
        assert cheaper.total_tickets < uniform.total_tickets
        assert cheaper.total_cost < uniform.total_cost
