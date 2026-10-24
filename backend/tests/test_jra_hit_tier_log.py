"""JRA 推奨（hit_tier）前向き記録のユニットテスト。

守りたいのは 3 点:

- **発走時刻を過ぎたレースを記録しない**こと。締切間際〜発走後のオッズを混ぜると
  記録そのものが look-ahead になり、検証窓としての価値が消える
- **購入指針（tier）・指数信頼度を算出しない**こと（2026-10-24 廃止）。
  記録は「★推奨」タブ（複勝ピック）の材料として続けるが、判定値は NULL で書く
- **全出走馬の順位と人気は残す**こと。複勝ピックの月次集計がこれを読む
"""

from __future__ import annotations

from datetime import datetime, timedelta

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

    def test_tierと信頼度は算出しない(self) -> None:
        d = evaluate_race(
            win_odds={1: 1.3, 2: 4.0, 3: 6.0, 4: 12.0, 5: 30.0},
            index_by_hn=self._idx(),
            win_probs={1: 0.6, 2: 0.2, 3: 0.1, 4: 0.05, 5: 0.05},
            head_count=5,
        )
        assert d.tier is None
        assert d.bet_type is None
        assert d.confidence_score is None
        assert d.market_agree is None
        assert d.skip_reason == "tier_retired"  # 推奨は出さない（is_recommended=False）

    def test_順位と人気は残す(self) -> None:
        d = evaluate_race(
            win_odds={1: 30.0, 2: 1.8, 3: 6.0, 4: 12.0, 5: 40.0},
            index_by_hn=self._idx(),
            win_probs={},
            head_count=None,
        )
        assert d.top1_horse_number == 1
        assert d.top1_win_odds == 30.0
        assert d.index_ranks[1] == 1
        assert d.pop_ranks[2] == 1

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


class TestRuleVersion:
    def test_廃止後の世代になっている(self) -> None:
        """tier を持つ旧世代（"hit_tier,..."）と混ざらないこと。"""
        assert RULE_VERSION.startswith("snapshot_only,")
        assert "cut=" in RULE_VERSION

    def test_リードは10分(self) -> None:
        """発走10分前は「賭けられる時点」かつオッズ充足がほぼ頭打ちになる点。

        変えるときは `jra_hit_tier_log` の docstring の実測表も更新すること
        （記録の意味が変わるため）。
        """
        assert SNAPSHOT_LEAD_MINUTES == 10
