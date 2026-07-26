"""単勝×複勝オッズ裁定 予備検証(`jra_odds_cross_bettype_arbitrage.py`) ユニットテスト

DB接続不要。小さな合成例で Harville 複勝確率計算の健全性
（レース全体の P(3着以内) 合計 ≈ 3.0 等）と、本スクリプトが実装する
市場複勝オッズ→implied probability 正規化ロジックの整合性を検証する。

Harville 複勝確率そのものは新規実装せず、既に本番横断で使われている
`src.betting.odds_model._harville_place_probs` / `harville_win_probs_from_odds`
（`tests/test_odds_model.py` で検証済み・`scripts/validate_odds_approximation.py`
等からも import される共有実装）をそのまま利用する。ここでは
(1) その共有実装の健全性を改めて確認し、(2) 本スクリプト固有の
市場複勝オッズ正規化関数 `normalize_market_place_probs` をテストする。
"""

from __future__ import annotations

import pytest

from scripts.jra_odds_cross_bettype_arbitrage import normalize_market_place_probs
from src.betting.odds_model import _harville_place_probs, harville_win_probs_from_odds

# ---------------------------------------------------------------------------
# 健全性チェック1: 既存 Harville 実装の P(3着以内) 合計 ≈ 3.0
# ---------------------------------------------------------------------------


class TestHarvilleSumHealthCheck:
    """本スクリプトが依拠する共有 Harville 実装の最重要整合性チェック。"""

    def test_sum_equals_three_uniform_8horses(self) -> None:
        """8頭・全馬均等ならP(3着以内)合計は3.0。"""
        win_probs = [1.0 / 8] * 8
        place = _harville_place_probs(win_probs, n=8)
        assert sum(place) == pytest.approx(3.0, abs=1e-8)

    def test_sum_equals_three_skewed_8horses(self) -> None:
        """8頭・偏った強さでも合計は3.0（対称性に依存しない）。"""
        win_probs = harville_win_probs_from_odds([1.5, 3.0, 5.0, 8.0, 12.0, 20.0, 35.0, 60.0])
        place = _harville_place_probs(win_probs, n=8)
        assert sum(place) == pytest.approx(3.0, abs=1e-8)

    @pytest.mark.parametrize("n", [8, 10, 12, 14, 16, 18])
    def test_sum_equals_three_various_head_counts(self, n: int) -> None:
        """8頭以上の様々な頭数で合計が3.0に収束する。"""
        odds = [1.5 + 3.0 * i for i in range(n)]
        win_probs = harville_win_probs_from_odds(odds)
        place = _harville_place_probs(win_probs, n=n)
        assert sum(place) == pytest.approx(3.0, abs=1e-8)

    def test_sum_equals_two_below_8horses(self) -> None:
        """8頭未満はJRAルールで2着払いのため合計は2.0（3着以内の定義が変わる）。

        本スクリプトは8頭未満のレースを解析対象から除外するが、
        共有実装自体がJRAの2着払いルールを正しく反映していることを確認する。
        """
        win_probs = harville_win_probs_from_odds([1.5, 3.0, 5.0, 8.0, 12.0, 20.0, 35.0])
        place = _harville_place_probs(win_probs, n=7)
        assert sum(place) == pytest.approx(2.0, abs=1e-8)

    def test_higher_win_prob_higher_place_prob(self) -> None:
        """勝率が高い馬ほど複勝確率も高い（単調性）。"""
        win_probs = harville_win_probs_from_odds([1.5, 3.0, 5.0, 8.0, 12.0, 20.0, 35.0, 60.0])
        place = _harville_place_probs(win_probs, n=8)
        for i in range(len(place) - 1):
            assert place[i] > place[i + 1]

    def test_all_probs_between_0_and_1(self) -> None:
        win_probs = harville_win_probs_from_odds([1.8, 4.0, 6.0, 9.0, 15.0, 25.0, 40.0, 80.0, 120.0])
        place = _harville_place_probs(win_probs, n=9)
        for p in place:
            assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# 健全性チェック2: 市場複勝オッズ → implied probability 正規化
# ---------------------------------------------------------------------------


class TestNormalizeMarketPlaceProbs:
    """`normalize_market_place_probs` のテスト。

    複勝は3頭が的中するため、単純な 1/odds ではなく
    「1/oddsの合計が理論上 3/(1-控除率) に対応する」ことを利用し、
    1/odds を合計3.0になるよう正規化して implied probability を得る。
    """

    def test_sums_to_three(self) -> None:
        """出力の合計は常にちょうど3.0（8頭以上・正規化の定義通り）。"""
        place_odds = [1.2, 1.5, 2.0, 3.0, 5.0, 8.0, 15.0, 30.0]
        probs = normalize_market_place_probs(place_odds)
        assert sum(probs) == pytest.approx(3.0, abs=1e-9)

    def test_lower_odds_higher_prob(self) -> None:
        """低い複勝オッズ(=人気)ほど implied probability が高い。"""
        place_odds = [1.2, 2.0, 5.0, 15.0]
        probs = normalize_market_place_probs(place_odds)
        assert probs[0] > probs[1] > probs[2] > probs[3]

    def test_uniform_odds_uniform_probs(self) -> None:
        """全馬同オッズなら implied probability も均等。"""
        place_odds = [4.0] * 8
        probs = normalize_market_place_probs(place_odds)
        expected = 3.0 / 8
        for p in probs:
            assert p == pytest.approx(expected, abs=1e-9)

    def test_all_probs_non_negative(self) -> None:
        place_odds = [1.1, 1.3, 1.8, 2.5, 4.0, 7.0, 12.0, 25.0, 50.0, 99.0]
        probs = normalize_market_place_probs(place_odds)
        for p in probs:
            assert p >= 0.0

    def test_single_horse_edge_case(self) -> None:
        """1頭のみでも正規化は破綻しない（合計3.0に張り付く=極端値だが例外にはしない）。"""
        probs = normalize_market_place_probs([1.1])
        assert probs[0] == pytest.approx(3.0, abs=1e-9)
