"""JRA 推奨（hit_tier）前向き記録のユニットテスト。

守りたいのは 3 点:

- **発走時刻を過ぎたレースを記録しない**こと。締切間際〜発走後のオッズを混ぜると
  記録そのものが look-ahead になり、検証窓としての価値が消える
- **tier 判定を本番と同じ関数から引く**こと。条件を記録側に書き写すと、
  本番の閾値を変えたときに記録だけ古い条件で残る
- **推奨が出なかったレース（tier=C）も記録する**こと。hit_tier は C を見送るので、
  棄権側が無いと「見送って正解だったか」を一切測れない
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.indices.confidence import calculate_recommend_rank
from src.services.jra_hit_tier_log import (
    JST,
    RULE_VERSION,
    SNAPSHOT_LEAD_MINUTES,
    evaluate_race,
    index_ranks,
    is_snapshot_due,
    parse_post_datetime,
    popularity_ranks,
)


class TestParsePostDatetime:
    def test_JSTの発走時刻になる(self) -> None:
        assert parse_post_datetime("20260815", "1425") == datetime(
            2026, 8, 15, 14, 25, tzinfo=JST
        )

    def test_発走時刻が無い形式はNone(self) -> None:
        assert parse_post_datetime("20260815", None) is None
        assert parse_post_datetime("20260815", "") is None
        assert parse_post_datetime("20260815", "14:25") is None
        assert parse_post_datetime("20260815", "145") is None

    def test_あり得ない時刻はNone(self) -> None:
        assert parse_post_datetime("20260815", "2599") is None


class TestIsSnapshotDue:
    def _now(self) -> datetime:
        return datetime(2026, 8, 15, 14, 20, tzinfo=JST)

    def test_窓の中なら撮る(self) -> None:
        post = self._now() + timedelta(minutes=SNAPSHOT_LEAD_MINUTES - 1)
        assert is_snapshot_due(post, self._now()) is True

    def test_窓の縁ちょうども撮る(self) -> None:
        post = self._now() + timedelta(minutes=SNAPSHOT_LEAD_MINUTES)
        assert is_snapshot_due(post, self._now()) is True

    def test_窓より前はまだ撮らない(self) -> None:
        post = self._now() + timedelta(minutes=SNAPSHOT_LEAD_MINUTES + 1)
        assert is_snapshot_due(post, self._now()) is False

    def test_発走時刻ちょうどは撮らない(self) -> None:
        """🔴 締切後のオッズが混ざると記録が look-ahead になる。"""
        assert is_snapshot_due(self._now(), self._now()) is False

    def test_発走後は撮らない(self) -> None:
        post = self._now() - timedelta(minutes=1)
        assert is_snapshot_due(post, self._now()) is False

    def test_発走時刻不明は対象外(self) -> None:
        assert is_snapshot_due(None, self._now()) is False


class TestRanks:
    def test_指数は降順_同値は馬番の小さい方が上(self) -> None:
        assert index_ranks({3: 60.0, 1: 60.0, 2: 55.0}) == {1: 1, 3: 2, 2: 3}

    def test_人気は昇順_同値は馬番の小さい方が上(self) -> None:
        assert popularity_ranks({3: 2.0, 1: 2.0, 2: 9.9}) == {1: 1, 3: 2, 2: 3}


class TestEvaluateRace:
    def _idx(self) -> dict[int, float]:
        return {1: 70.0, 2: 60.0, 3: 55.0, 4: 50.0, 5: 45.0}

    def test_指数1位が1番人気なら市場一致(self) -> None:
        d = evaluate_race(
            win_odds={1: 2.1, 2: 4.0, 3: 6.0, 4: 12.0, 5: 30.0},
            index_by_hn=self._idx(),
            win_probs={1: 0.4, 2: 0.25, 3: 0.2, 4: 0.1, 5: 0.05},
            head_count=5,
        )
        assert d.market_agree is True
        assert d.top1_horse_number == 1
        assert d.tier in ("S", "A", "B")
        assert d.skip_reason is None

    def test_市場乖離ならC系で棄権になる(self) -> None:
        d = evaluate_race(
            win_odds={1: 30.0, 2: 1.8, 3: 6.0, 4: 12.0, 5: 40.0},
            index_by_hn=self._idx(),
            win_probs={1: 0.4, 2: 0.25, 3: 0.2, 4: 0.1, 5: 0.05},
            head_count=5,
        )
        assert d.market_agree is False
        assert d.tier in ("C", "C+")
        if d.tier == "C":
            assert d.skip_reason == "tier_c"

    def test_断然人気はmarket_agreeを問わずS(self) -> None:
        """本番 `calculate_recommend_rank` の「単勝<1.5 は無条件 S」を記録側でも踏む。"""
        d = evaluate_race(
            win_odds={1: 1.3, 2: 4.0, 3: 6.0, 4: 12.0, 5: 30.0},
            index_by_hn=self._idx(),
            win_probs={1: 0.6, 2: 0.2, 3: 0.1, 4: 0.05, 5: 0.05},
            head_count=5,
        )
        assert d.tier == "S"
        assert d.bet_type == "win"

    def test_指数が無ければno_index(self) -> None:
        d = evaluate_race(win_odds={1: 2.0}, index_by_hn={}, win_probs={}, head_count=5)
        assert d.skip_reason == "no_index"
        assert d.tier is None

    def test_オッズが無ければno_odds(self) -> None:
        d = evaluate_race(
            win_odds={}, index_by_hn=self._idx(),
            win_probs={1: 0.4, 2: 0.25, 3: 0.2, 4: 0.1, 5: 0.05}, head_count=5,
        )
        assert d.skip_reason == "no_odds"
        assert d.market_agree is None

    @pytest.mark.parametrize("odds1", [1.3, 2.1, 30.0])
    def test_tierは本番関数と一致する(self, odds1: float) -> None:
        """記録側で条件を書き写していないことの確認。"""
        d = evaluate_race(
            win_odds={1: odds1, 2: 4.0, 3: 6.0, 4: 12.0, 5: 30.0},
            index_by_hn=self._idx(),
            win_probs={1: 0.4, 2: 0.25, 3: 0.2, 4: 0.1, 5: 0.05},
            head_count=5,
        )
        expected = calculate_recommend_rank(
            d.confidence_score, d.win_prob_top, d.top1_win_odds,
            d.market_agree, d.entropy_norm,
        )
        assert d.tier == expected


class TestRuleVersion:
    def test_閾値が署名に入っている(self) -> None:
        """閾値を変えたら rule_version が変わり、集計時に世代が分かれる。"""
        assert RULE_VERSION.startswith("hit_tier,")
        assert "gap=" in RULE_VERSION
        assert "cut=" in RULE_VERSION

    def test_リードは10分(self) -> None:
        """発走10分前は「賭けられる時点」かつオッズ充足がほぼ頭打ちになる点。

        変えるときは `jra_hit_tier_log` の docstring の実測表も更新すること
        （記録の意味が変わるため）。
        """
        assert SNAPSHOT_LEAD_MINUTES == 10
