"""netkeiba スクレイパ共通層の不変条件を固定する。

なぜ必要か（2026-09-06・統合 Phase 2 後半）:
    netkeiba のスクレイパを sekito から kiseki へ移すにあたり、
    **移設によって初めて壊れる 3 点**をここで機械的に押さえる。

      1. IP 制限ゲート — sekito では `scheduler.js` が実行前に弾いていた。
         kiseki は cron 起動なので門番が居ない。持ってこないと無防備になる。
      2. 時間帯別レート制限の時刻 — sekito のコンテナは JST、kiseki は UTC。
         素の `datetime.now()` を使うと 9 時間ずれた設定が当たる。
      3. 文字コード — ホストごとに違う。決め打ちすると黙って化ける。

    どれも例外を出さずに壊れるので、テストで固定しないと気づけない。
"""

from __future__ import annotations

import pytest

from src.scrapers.netkeiba.decode import decode_page
from src.scrapers.netkeiba.ip_restriction import (
    IP_RESTRICTION_PATTERNS,
    IPRestricted,
    is_restricted,
    looks_like_ip_restriction,
    require_not_restricted,
    restricted_keys,
)
from src.scrapers.netkeiba.rate_limiter import RateLimiter, config_for_hour

# --------------------------------------------------------------------------
# 文字コード
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content: bytes, content_type: str = "", url: str = "http://x/"):
        self.content = content
        self.headers = {"content-type": content_type}
        self.url = url


def test_ヘッダのcharsetを最優先する():
    body = "馬名テスト".encode("euc-jp")
    r = _FakeResponse(body, "text/html; charset=EUC-JP")
    assert decode_page(r) == "馬名テスト"


def test_metaのcharsetを見る():
    body = b'<meta charset="euc-jp">' + "調教".encode("euc-jp")
    assert "調教" in decode_page(_FakeResponse(body))


def test_ヘッダもmetaも無ければUTF8を先に試す():
    """race.netkeiba.com / nar.netkeiba.com は UTF-8。"""
    assert decode_page(_FakeResponse("パドック".encode())) == "パドック"


def test_UTF8で読めなければEUC_JPへ落ちる():
    """db.netkeiba.com は EUC-JP。

    🔴 移設元は euc-jp 決め打ち + errors="replace" だったため、UTF-8 のホストが
    4 か月間 U+FFFD 入りで DB に入り続けた。厳密デコードで候補を順に試すこと。
    """
    got = decode_page(_FakeResponse("血統".encode("euc-jp")))
    assert got == "血統"
    assert "�" not in got


def test_誤ったcharset指定でも次の候補で読める():
    """ヘッダが嘘をついていても、厳密デコードが弾いて次へ進む。"""
    r = _FakeResponse("先行力".encode(), "text/html; charset=EUC-JP")
    assert decode_page(r) == "先行力"


def test_未知のcharsetでも落ちない():
    r = _FakeResponse("末脚".encode(), "text/html; charset=x-not-a-real-encoding")
    assert decode_page(r) == "末脚"


# --------------------------------------------------------------------------
# IP 制限ゲート
# --------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _FakeSession:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def execute(self, *_a, **_kw):
        return _FakeResult(self.rows)


def test_制限キーが無ければ通す():
    assert is_restricted(_FakeSession()) is False
    require_not_restricted(_FakeSession())  # 例外が出ないこと


def test_どれか1つでもtrueなら止める_fail_closed():
    """🔴 自分の環境のキーだけを見てはいけない。

    誤検知（古い `_local` が残る）→ しばらく取りに行かない。戻せる。
    見逃し（自分のキーだけ見る）→ IP 制限を食らって伸ばす。戻せない。
    sekito の `scheduler.js` も `LIKE 'netkeiba_ip_restricted%'` で全部見ている。
    """
    s = _FakeSession([("netkeiba_ip_restricted_local",)])
    assert is_restricted(s) is True
    with pytest.raises(IPRestricted):
        require_not_restricted(s)


def test_止めた原因のキー名を返す():
    """古いキーが残って止まっているとき、どれが原因か分からないと直せない。"""
    s = _FakeSession([("netkeiba_ip_restricted_local",),
                      ("netkeiba_ip_restricted_server",)])
    assert restricted_keys(s) == [
        "netkeiba_ip_restricted_local", "netkeiba_ip_restricted_server",
    ]
    with pytest.raises(IPRestricted, match="netkeiba_ip_restricted_server"):
        require_not_restricted(s, context="netkeiba-index")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP 403 Forbidden", True),
        ("Access Denied", True),
        ("HTTP 400 (IPブロックまたはBot検出の可能性)", True),
        ("HTTP 500 Internal Server Error", False),
        ("jQuery 読み込みタイムアウト", False),
        ("", False),
        (None, False),
    ],
)
def test_IP制限とみなす文言(message, expected):
    """⚠️ 誤検知するとスクレイプが止まる。**確実なものだけ**を入れる。

    「jQuery 読み込みタイムアウト」はページ遅延やレース未確定でも起きるため
    移設元でも意図的に外されている。戻さないこと。
    """
    assert looks_like_ip_restriction(message) is expected


def test_タイムアウトはIP制限とみなさない():
    assert not any("timeout" in p.lower() for p in IP_RESTRICTION_PATTERNS)


# --------------------------------------------------------------------------
# レート制限
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("hour", "min_interval", "max_requests"),
    [
        (3, 3.0, 15),    # 深夜
        (7, 4.0, 12),    # 早朝
        (10, 5.0, 10),   # 日中（開催中・いちばん保守的）
        (14, 5.0, 10),
        (16, 4.0, 12),   # 午後
        (22, 3.0, 15),   # 夜
    ],
)
def test_時間帯別の設定が移設元と一致する(hour, min_interval, max_requests):
    c = config_for_hour(hour)
    assert (c.min_interval, c.max_requests) == (min_interval, max_requests)


def test_全ての時刻に設定がある():
    for hour in range(24):
        assert config_for_hour(hour) is not None


def test_日中がいちばん保守的():
    """🔴 kiseki の backend コンテナは UTC。

    素の `datetime.now().hour` を使うと JST 10 時（日中）に UTC 1 時＝深夜の
    設定が当たり、**開催中に最も速く叩く**という最悪の取り違えになる。
    `config_for_hour()` は JST で判定する。
    """
    daytime = config_for_hour(10)
    for hour in (3, 7, 16, 22):
        assert config_for_hour(hour).min_interval <= daytime.min_interval
        assert config_for_hour(hour).max_requests >= daytime.max_requests


def test_待機なしで上限まで数える():
    """履歴の管理だけを見る（実待機はしない）。"""
    limiter = RateLimiter(config_for_hour(3))
    assert limiter.request_count == 0
