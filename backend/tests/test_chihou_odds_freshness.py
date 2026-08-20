"""地方オッズ鮮度判定の検査。

ここが壊れると「止まっているのに緑」「終わったレースが全部赤」のどちらかになり、
どちらも信号として無価値になる。境界とレース終了後の扱いを固定する。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.services.chihou_odds_freshness import (
    STATUS_CLOSED,
    STATUS_DELAYED,
    STATUS_LIVE,
    STATUS_MISSING,
    STATUS_STALE,
    classify_odds_freshness,
    post_time_to_utc,
)

NOW = datetime(2026, 8, 20, 10, 50, 0)  # naive UTC = 19:50 JST
POST = datetime(2026, 8, 20, 11, 50, 0)  # naive UTC = 20:50 JST（未発走）


def _classify(age_seconds: int | None, *, post=POST, now=NOW):
    last = None if age_seconds is None else now - timedelta(seconds=age_seconds)
    return classify_odds_freshness(last_fetched_at=last, now_utc=now, post_at_utc=post)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, STATUS_LIVE),
        (60, STATUS_LIVE),
        (300, STATUS_LIVE),      # 境界: 5分ちょうどは緑
        (301, STATUS_DELAYED),
        (899, STATUS_DELAYED),
        (900, STATUS_STALE),     # 境界: 15分ちょうどで赤（watchdog の STALL_MINUTES と同値）
        (4 * 3600, STATUS_STALE),
    ],
)
def test_age_thresholds(age: int, expected: str) -> None:
    assert _classify(age).status == expected


def test_age_seconds_is_reported() -> None:
    assert _classify(123).age_seconds == 123


def test_missing_when_never_fetched() -> None:
    """1件も無いのは異常ではない（翌日以降のレースは取得前で当然0件）。"""
    got = _classify(None)
    assert got.status == STATUS_MISSING
    assert got.age_seconds is None
    assert got.last_fetched_at is None


def test_started_race_is_closed_not_stale() -> None:
    """発走済みの更新停止は正常。ここを赤にすると終わったレースが全部赤くなる。"""
    now = POST + timedelta(hours=3)
    got = classify_odds_freshness(
        last_fetched_at=POST - timedelta(minutes=1), now_utc=now, post_at_utc=POST
    )
    assert got.status == STATUS_CLOSED
    assert got.age_seconds == 3 * 3600 + 60


def test_exactly_at_post_time_is_closed() -> None:
    got = classify_odds_freshness(
        last_fetched_at=POST - timedelta(minutes=1), now_utc=POST, post_at_utc=POST
    )
    assert got.status == STATUS_CLOSED


def test_grace_keeps_delayed_race_open() -> None:
    """発走が遅れた場合に猶予で「まだ発走前」として扱えること。"""
    got = classify_odds_freshness(
        last_fetched_at=POST - timedelta(minutes=30),
        now_utc=POST + timedelta(minutes=2),
        post_at_utc=POST,
        grace=timedelta(minutes=5),
    )
    assert got.status == STATUS_STALE


def test_unknown_post_time_still_judged() -> None:
    """発走時刻が取れないレースでも鮮度判定は行う（不明を理由に緑へ倒さない）。"""
    got = classify_odds_freshness(
        last_fetched_at=NOW - timedelta(hours=4), now_utc=NOW, post_at_utc=None
    )
    assert got.status == STATUS_STALE


def test_future_timestamp_does_not_go_negative() -> None:
    got = classify_odds_freshness(
        last_fetched_at=NOW + timedelta(minutes=5), now_utc=NOW, post_at_utc=POST
    )
    assert got.age_seconds == 0
    assert got.status == STATUS_LIVE


def test_the_2026_08_20_outage_is_red() -> None:
    """実際に4時間51分止まった局面を赤と判定すること（回帰検査）。

    川崎10R は 19:40 発走。19:42 時点で最終取得は 14:55 だった。
    """
    now = datetime(2026, 8, 20, 10, 30)          # 19:30 JST（発走10分前）
    last = datetime(2026, 8, 20, 5, 55)          # 14:55 JST
    post = post_time_to_utc("20260820", "1940")
    got = classify_odds_freshness(last_fetched_at=last, now_utc=now, post_at_utc=post)
    assert got.status == STATUS_STALE
    assert got.age_seconds == 4 * 3600 + 35 * 60


class TestPostTimeToUtc:
    def test_converts_jst_to_utc(self) -> None:
        assert post_time_to_utc("20260820", "1940") == datetime(2026, 8, 20, 10, 40)

    def test_crosses_the_date_line_backwards(self) -> None:
        """ナイター開催の 00:20 発走は前日の UTC になる。"""
        assert post_time_to_utc("20260821", "0020") == datetime(2026, 8, 20, 15, 20)

    @pytest.mark.parametrize(
        ("date", "post_time"),
        [(None, "1940"), ("20260820", None), ("20260820", ""), ("2026082", "1940"),
         ("20260820", "194"), ("20260820", "abcd"), ("20260820", "2599")],
    )
    def test_returns_none_for_unusable_input(self, date, post_time) -> None:
        assert post_time_to_utc(date, post_time) is None
