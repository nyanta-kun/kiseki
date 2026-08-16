"""netkeirin 売上の Discord 通知（2026-08-16 追加）。

朝 9:40 の売上取り込み（`scripts/scrape_netkeirin_sales.sh`）のあとに、前日の
販売pt・販売有償pt・売上・当月累計を Discord へ送る。

## 固定すること

1. **売上率が Web と食い違わない**こと。売上は「販売*有償*pt × 0.30」で、
   率の正本は `keirin_router.NETKEIRIN_REVENUE_RATE`。スクレイプ側は VPS で
   keirin の venv（FastAPI も SQLAlchemy も無い）で動くため import できず、
   **定数を写している**。ずれると Web と Discord で売上が違う数字になる
2. **販売pt と 販売有償pt を両方出す**こと。売上になるのは有償ptだけで、
   `sold_points` には無償ptが混ざる（片方だけ見せると収益を誤読する）
3. **通知の失敗で取り込みを落とさない**こと。売上は既に DB にあり、通知はその報告
4. **行が無い日は送らない**こと。0円と書くと「売れなかった」と誤読する
   （実際は開催が無いか netkeirin 側の集計がまだ）
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "scrape_netkeirin_sales.py"
_spec = importlib.util.spec_from_file_location("scrape_netkeirin_sales", _PATH)
assert _spec and _spec.loader
sales = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sales)


def _summary(**kw) -> dict:
    base = {
        "sale_date": "20260815", "n_sold": 85,
        "sold_points": 25500, "sold_paid_points": 10920,
        "month_n_days": 15, "month_sold_points": 300000,
        "month_sold_paid_points": 120000,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 売上率
# ---------------------------------------------------------------------------

def test_売上率はWebと同じ値():
    """🔴 ずれると同じ日の売上が画面と Discord で違う額になる。"""
    from src.api.keirin_router import NETKEIRIN_REVENUE_RATE

    assert sales.REVENUE_RATE == NETKEIRIN_REVENUE_RATE


# ---------------------------------------------------------------------------
# 本文
# ---------------------------------------------------------------------------

def test_売上は有償ptから計算する():
    msg = sales.build_sales_message(_summary())
    assert f"{round(10920 * 0.30):,}" in msg          # 3,276円
    # 販売pt(25,500)をそのまま掛けた額は出てはいけない
    assert f"{round(25500 * 0.30):,} 円" not in msg


def test_販売ptと販売有償ptを両方出す():
    msg = sales.build_sales_message(_summary())
    assert "販売pt" in msg and "販売有償pt" in msg
    assert "25,500" in msg and "10,920" in msg


def test_当月の総売上を出す():
    msg = sales.build_sales_message(_summary())
    assert f"{round(120000 * 0.30):,}" in msg          # 36,000円
    assert "累計" in msg


def test_日付は年月日で出す():
    assert "2026-08-15" in sales.build_sales_message(_summary())


def test_売上ゼロでも本文は組める():
    msg = sales.build_sales_message(
        _summary(n_sold=0, sold_points=0, sold_paid_points=0))
    assert "0 円" in msg


# ---------------------------------------------------------------------------
# 通知の可否
# ---------------------------------------------------------------------------

def test_webhookが未設定なら送らない(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_NETKEIRIN", raising=False)
    monkeypatch.setattr(sales, "fetch_sales_summary",
                        lambda d: pytest.fail("URL が無いのに DB を読んだ"))
    assert sales.notify_sales(date(2026, 8, 15)) is False


def test_当日行が無ければ送らない(monkeypatch):
    """0円と書くと「売れなかった」と誤読する。開催が無い日・集計待ちの日がある。"""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_NETKEIRIN", "https://example.invalid/hook")
    monkeypatch.setattr(sales, "fetch_sales_summary", lambda d: None)
    monkeypatch.setattr(sales.requests, "post",
                        lambda *a, **k: pytest.fail("行が無いのに送信した"))
    assert sales.notify_sales(date(2026, 8, 15)) is False


def test_送信に失敗しても例外を投げない(monkeypatch):
    """🔴 通知は取り込みの付随物。ここで落とすとスクレイプが失敗したように見える。"""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_NETKEIRIN", "https://example.invalid/hook")
    monkeypatch.setattr(sales, "fetch_sales_summary", lambda d: _summary())

    def _boom(*a, **k):
        raise sales.requests.RequestException("boom")

    monkeypatch.setattr(sales.requests, "post", _boom)
    assert sales.notify_sales(date(2026, 8, 15)) is False


def test_DB読み取りに失敗しても例外を投げない(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_NETKEIRIN", "https://example.invalid/hook")

    def _boom(_d):
        raise RuntimeError("db down")

    monkeypatch.setattr(sales, "fetch_sales_summary", _boom)
    assert sales.notify_sales(date(2026, 8, 15)) is False


def test_成功したらTrueを返す(monkeypatch):
    sent: dict = {}
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_NETKEIRIN", "https://example.invalid/hook")
    monkeypatch.setattr(sales, "fetch_sales_summary", lambda d: _summary())

    class _Resp:
        def raise_for_status(self):
            return None

    def _post(url, json=None, timeout=None):
        sent["url"] = url
        sent["content"] = (json or {}).get("content", "")
        return _Resp()

    monkeypatch.setattr(sales.requests, "post", _post)
    assert sales.notify_sales(date(2026, 8, 15)) is True
    assert "販売有償pt" in sent["content"]


# ---------------------------------------------------------------------------
# 呼び出し条件（黙って通知が消える／溢れるのを防ぐ）
# ---------------------------------------------------------------------------

def test_複数日のバックフィルでは通知しない():
    """過去分を取り直すたびに何十件も飛ぶのを防ぐ。"""
    src = _PATH.read_text(encoding="utf-8")
    assert "start == end" in src


def test_日別を取っていない回は通知しない():
    """売上の数字は日別テーブルにしか無い。レース別だけ取った回に送ると
    取り込んでいない日の数字を報告することになる。"""
    src = _PATH.read_text(encoding="utf-8")
    assert "want_day and not args.no_notify" in src


def test_シェルがwebhookを渡している():
    """🔴 python 側は env からしか URL を読めない。export を外すと
    **通知だけが静かに止まる**（取り込みは成功し続ける）。"""
    sh = (_PATH.parents[2] / "scripts" / "scrape_netkeirin_sales.sh").read_text(
        encoding="utf-8")
    assert "DISCORD_WEBHOOK_URL_NETKEIRIN" in sh
    assert "export DISCORD_WEBHOOK_URL_NETKEIRIN" in sh
