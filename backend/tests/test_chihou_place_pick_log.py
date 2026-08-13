"""地方競馬 注目馬 前向き記録のユニットテスト。

守りたいのは 2 点:

- **発走時刻を過ぎたレースを記録しない**こと。締切間際〜発走後のオッズを混ぜると
  記録そのものが look-ahead になり、検証窓としての価値が消える（台帳 10.3.1）
- **判定を本番と同じ関数から引く**こと。条件を記録側に書き写すと、本番の閾値を
  変えたときに記録だけ古い条件で残る
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.indices.buy_signal import (
    CHIHOU_OPEN_RACE_MAX_TOP3_SHARE,
    CHIHOU_PICK_MAX_INDEX_RANK,
    CHIHOU_PICK_MAX_PER_RACE,
    CHIHOU_PICK_MIN_POP_RANK,
    CHIHOU_PLACE_MIN_HEAD_COUNT,
)
from src.services.chihou_place_pick_log import (
    JST,
    PICK_RULE_VERSION,
    SNAPSHOT_LEAD_MINUTES,
    UPSET_POP_RANK,
    RaceDecision,
    _index_ranks,
    evaluate_race,
    is_snapshot_due,
    parse_post_datetime,
)


class TestParsePostDatetime:
    def test_JSTの発走時刻になる(self) -> None:
        dt = parse_post_datetime("20260815", "1425")
        assert dt == datetime(2026, 8, 15, 14, 25, tzinfo=JST)

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

    def test_窓の外_まだ早い(self) -> None:
        post = self._now() + timedelta(minutes=SNAPSHOT_LEAD_MINUTES + 1)
        assert is_snapshot_due(post, self._now()) is False

    def test_発走時刻ちょうどは撮らない(self) -> None:
        assert is_snapshot_due(self._now(), self._now()) is False

    def test_発走後は絶対に撮らない(self) -> None:
        """締切間際の資金移動が混ざると記録自体が look-ahead になる（台帳 10.3.1）。"""
        for late in (1, 5, 60, 600):
            post = self._now() - timedelta(minutes=late)
            assert is_snapshot_due(post, self._now()) is False

    def test_発走時刻不明は対象外(self) -> None:
        assert is_snapshot_due(None, self._now()) is False

    def test_リード分数は上書きできる(self) -> None:
        post = self._now() + timedelta(minutes=9)
        assert is_snapshot_due(post, self._now(), lead_minutes=10) is True
        assert is_snapshot_due(post, self._now(), lead_minutes=5) is False


class TestIndexRanks:
    def test_指数の高い順に順位が付く(self) -> None:
        assert _index_ranks([(3, 55.0), (1, 70.0), (7, 60.0)]) == {1: 1, 7: 2, 3: 3}

    def test_同値は馬番の小さい方が上位(self) -> None:
        """本番 rank_by_hn と同じ「先着」規則。ここがずれると記録と表示が食い違う。"""
        assert _index_ranks([(5, 60.0), (2, 60.0), (9, 60.0)]) == {2: 1, 5: 2, 9: 3}

    def test_空なら空(self) -> None:
        assert _index_ranks([]) == {}


class TestEvaluateRace:
    """開いた10頭立て。人気薄（6番人気以下）に指数上位が混じっている状況を作る。"""

    ODDS = {1: 4.5, 2: 5.0, 3: 5.5, 4: 6.0, 5: 7.0, 6: 8.0, 7: 9.0, 8: 12.0, 9: 35.0, 10: 40.0}

    def _eval(self, index_by_hn: dict[int, float], **kw) -> RaceDecision:
        params = {"head_count": None, "registered_count": 10, **kw}
        return evaluate_race(win_odds=self.ODDS, index_by_hn=index_by_hn, **params)

    def test_人気薄で指数上位の馬が推奨される(self) -> None:
        # 9番（10番人気）と 7番（7番人気）が指数1位・2位
        d = self._eval({9: 80.0, 7: 75.0, 1: 70.0, 2: 65.0, 3: 60.0,
                        4: 55.0, 5: 50.0, 6: 45.0, 8: 40.0, 10: 35.0})
        assert d.skip_reason is None
        assert d.picked == [9, 7]
        assert d.top3_share is not None and d.top3_share < CHIHOU_OPEN_RACE_MAX_TOP3_SHARE

    def test_最大2頭に絞られる(self) -> None:
        # 6〜10番（すべて6番人気以下）が指数1〜5位＝適格5頭
        d = self._eval({6: 90.0, 7: 85.0, 8: 80.0, 9: 75.0, 10: 70.0,
                        1: 60.0, 2: 55.0, 3: 50.0, 4: 45.0, 5: 40.0})
        assert len(d.eligible) == CHIHOU_PICK_MAX_PER_RACE + 3
        assert d.picked == [6, 7]

    def test_人気馬しか指数上位にいなければ推奨しない(self) -> None:
        d = self._eval({1: 80.0, 2: 75.0, 3: 70.0, 4: 65.0, 5: 60.0,
                        6: 55.0, 7: 50.0, 8: 45.0, 9: 40.0, 10: 35.0})
        assert d.picked == []
        assert d.skip_reason == "no_candidate"

    def test_断然人気がいる閉じたレースは推奨しない(self) -> None:
        closed = {1: 1.2, 2: 4.0, 3: 6.0, 4: 20.0, 5: 30.0,
                  6: 40.0, 7: 50.0, 8: 60.0, 9: 70.0, 10: 80.0}
        d = evaluate_race(
            win_odds=closed,
            index_by_hn={9: 80.0, 7: 75.0, 1: 70.0},
            head_count=None,
            registered_count=10,
        )
        assert d.picked == []
        assert d.skip_reason == "closed_race"

    def test_7頭以下は複勝が2着までなので対象外(self) -> None:
        d = self._eval({9: 80.0, 7: 75.0}, registered_count=7)
        assert d.picked == []
        assert d.skip_reason == "small_field"

    def test_オッズが無ければ判定不能として記録する(self) -> None:
        d = evaluate_race(
            win_odds={}, index_by_hn={1: 80.0}, head_count=None, registered_count=10
        )
        assert d.picked == []
        assert d.skip_reason == "no_odds"

    def test_指数が無ければ判定不能として記録する(self) -> None:
        d = self._eval({})
        assert d.picked == []
        assert d.skip_reason == "no_index"

    def test_確定頭数があればそちらを優先する(self) -> None:
        """head_count はレース後にしか入らないが、入っていれば正になる。"""
        d = self._eval({9: 80.0, 7: 75.0}, head_count=6, registered_count=12)
        assert d.head_count_used == 6
        assert d.skip_reason == "small_field"


class TestRuleVersion:
    def test_本番の閾値がそのまま署名に入る(self) -> None:
        """署名が本番定数から作られていれば、閾値変更時に世代が自動で分かれる。"""
        assert f"pop>={CHIHOU_PICK_MIN_POP_RANK}" in PICK_RULE_VERSION
        assert f"idx<={CHIHOU_PICK_MAX_INDEX_RANK}" in PICK_RULE_VERSION
        assert f"share<{CHIHOU_OPEN_RACE_MAX_TOP3_SHARE}" in PICK_RULE_VERSION
        assert f"head>={CHIHOU_PLACE_MIN_HEAD_COUNT}" in PICK_RULE_VERSION
        assert f"max{CHIHOU_PICK_MAX_PER_RACE}" in PICK_RULE_VERSION

    def test_人気薄の線引きは判定と同じ(self) -> None:
        assert UPSET_POP_RANK == CHIHOU_PICK_MIN_POP_RANK
