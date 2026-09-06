"""取得状態の再試行ロジックを固定する。

なぜ必要か（2026-09-06・統合 Phase 2）:
    `should_fetch` は「取りに行くか」を決める唯一の関門で、ここが緩むと
    レートリミットに触れて IP 制限を食らい、締めすぎると欠損が埋まらない。
    移設で psycopg2 → SQLAlchemy に書き換えた箇所なので、**状態遷移が
    移設前と 1:1 であること**を機械的に固定しておく。

    移設前の実装: sekito `lib/sekito/utils/fetch_status_manager.py`
"""

from __future__ import annotations

from datetime import date

import pytest

from src.scrapers.fetch_status import (
    _SHOULD_FETCH_SQL,
    ALL_DATA_TYPES,
    FetchStatusManager,
    _norm_date,
    detect_race_cancellation,
)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    """`SELECT` 1 本ぶんだけを差し替える最小のスタブ。

    経過時間は DB が返す前提なので、スタブも「時間」を直接返す。
    """

    def __init__(self, row=None):
        self.row = row

    def execute(self, *_args, **_kwargs):
        return _FakeResult(self.row)


def _mgr(status=None, retry_count=0, age_hours=0.0):
    if status is None:
        return FetchStatusManager(_FakeSession(None))
    return FetchStatusManager(_FakeSession((status, retry_count, age_hours)))


def _should(mgr, data_type="kichiuma", **kw):
    return mgr.should_fetch(date(2026, 9, 6), "JTOK", 1, data_type, **kw)


def test_レコードが無ければ取りに行く():
    assert _should(_mgr()) is True


@pytest.mark.parametrize("status", ["fetched", "race_cancelled"])
def test_確定した状態は二度と取りに行かない(status):
    assert _should(_mgr(status, age_hours=999)) is False


def test_not_available_は既定6時間で再試行する():
    assert _should(_mgr("not_available", age_hours=5.9)) is False
    assert _should(_mgr("not_available", age_hours=6.1)) is True


def test_not_available_のtime_indexだけ1時間で再試行する():
    """タイム指数はレース直前に公開されることがあるため短い。"""
    dt = "netkeiba_time_index"
    assert _should(_mgr("not_available", age_hours=0.9), dt) is False
    assert _should(_mgr("not_available", age_hours=1.1), dt) is True


def test_not_yet_published_は1時間で再試行する():
    assert _should(_mgr("not_yet_published", age_hours=0.9)) is False
    assert _should(_mgr("not_yet_published", age_hours=1.1)) is True


def test_fetch_failed_は1時間空けて最大3回まで():
    assert _should(_mgr("fetch_failed", retry_count=0, age_hours=0.5)) is False
    assert _should(_mgr("fetch_failed", retry_count=0, age_hours=1.1)) is True
    assert _should(_mgr("fetch_failed", retry_count=2, age_hours=1.1)) is True
    # 3 回で打ち切り。ここが無いと壊れたレースを毎回叩き続ける
    assert _should(_mgr("fetch_failed", retry_count=3, age_hours=999)) is False


def test_force_はすべてを飛び越える():
    assert _should(_mgr("fetched", age_hours=0), force=True) is True


def test_updated_atがNULLでも取りに行く():
    """移設前は `updated_at and ...` で NULL を「経過済み」として扱っていた。"""
    mgr = FetchStatusManager(_FakeSession(("not_available", 0, None)))
    assert _should(mgr) is True


def test_経過時間はDB側のNOWで計算する():
    """🔴 Python の `datetime.now()` と比べてはいけない。

    2026-09-06 実測: DB は Asia/Tokyo だが kiseki の backend コンテナは UTC。
    naive 比較のまま移すと経過時間が 9 時間ぶん過小に出て、再試行が
    それだけ遅れる（例外は出ず、ただ取りに行かなくなる）。
    移設元 sekito のコンテナは JST だったので、この差は移設で初めて生まれた。
    """
    sql = str(_SHOULD_FETCH_SQL)
    assert "NOW() - updated_at" in sql, "経過時間の計算が SQL 側から消えている"
    assert "EXTRACT(EPOCH" in sql


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20260906", "2026-09-06"),
        ("2026-09-06", "2026-09-06"),
        (date(2026, 9, 6), "2026-09-06"),
    ],
)
def test_日付は_YYYY_MM_DD_に正規化される(raw, expected):
    """'YYYYMMDD' でも `date` でも同じ行を指すこと。ずれると取得済み判定が効かない。"""
    assert _norm_date(raw) == expected


def test_中止時に潰すデータタイプに移設した2種が入っている():
    """穴ぐさ・吉馬が抜けていると、中止レースを取りに行き続ける。"""
    assert "anagusa" in ALL_DATA_TYPES
    assert "kichiuma" in ALL_DATA_TYPES


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>このレースは中止になりました</p>", True),
        ("<p>開催中止</p>", True),
        ("<p>通常のレースページ</p>", False),
    ],
)
def test_中止の検出(html, expected):
    assert detect_race_cancellation(html) is expected
