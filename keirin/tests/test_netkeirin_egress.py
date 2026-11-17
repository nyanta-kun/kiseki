"""netkeirin の出口（Mac mini 経由）と入稿前チェックを固定する（2026-11-11）。

## 守りたいこと

1. プロキシは `NETKEIRIN_PROXY_URL` があるときだけ、**netkeirin の Session にだけ**掛かる
   （HTTPS_PROXY をグローバルに入れると DB・Discord まで Mac mini 経由になる）
2. 入稿前チェックが Cloudflare 認証切れ・出口断・セッション切れを取り違えない
3. 承認制（入稿案だけ作る）は netkeirin に一切通信しない（ログインもしない）
4. 入稿ツールに入れない日は、入稿を止めて**未入稿の一覧つきで知らせる**
5. 入稿が全件失敗した日も**必ず知らせる**（以前は成功0件だと無言だった）
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
import requests

from src import netkeirin_client as nc


# ── 1. プロキシの掛け方 ────────────────────────────────────────────────

def _with_env(monkeypatch, value: str | None):
    monkeypatch.setattr(nc, "_env", lambda k: (value or "") if k == nc.PROXY_ENV_KEY else "")
    monkeypatch.setattr(nc, "SESSION_FILE", nc.DATA_DIR / "__no_such_session__.json")


def test_no_proxy_when_unset(monkeypatch):
    _with_env(monkeypatch, None)
    c = nc.NetkeirinClient()
    assert not c.session.proxies
    assert c.proxy_url == ""


def test_proxy_only_on_session(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    _with_env(monkeypatch, "socks5h://127.0.0.1:18080")
    c = nc.NetkeirinClient()
    assert c.session.proxies == {"http": "socks5h://127.0.0.1:18080",
                                 "https": "socks5h://127.0.0.1:18080"}
    assert c.session.trust_env is False
    # 🔴 プロセス全体には広げない
    assert "HTTPS_PROXY" not in os.environ and "https_proxy" not in os.environ


def test_socks_without_pysocks_fails_loudly(monkeypatch):
    """PySocks が無いのに SOCKS を指定したら、作る時点で止める（無言の全件失敗を防ぐ）。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "socks":
            raise ImportError("no socks")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    _with_env(monkeypatch, "socks5h://127.0.0.1:18080")
    with pytest.raises(RuntimeError, match="PySocks"):
        nc.NetkeirinClient()


# ── 2. 入稿前チェックの分類 ────────────────────────────────────────────

@pytest.mark.parametrize("code,headers,text,expected", [
    (200, {}, "<a>ログアウト</a>", nc.PREFLIGHT_OK),
    (302, {"Location": "https://tool.syakenv2.netkeiba.com/bettool/auth/login.html"}, "",
     nc.PREFLIGHT_NEEDS_LOGIN),
    (302, {"Location": "https://nd-group.cloudflareaccess.com/cdn-cgi/access/login/x"}, "",
     nc.PREFLIGHT_CF_AUTH),
    (403, {"Server": "cloudflare"}, "", nc.PREFLIGHT_CF_AUTH),
    (403, {"cf-ray": "abc"}, "", nc.PREFLIGHT_CF_AUTH),
    (403, {"Server": "Apache"}, "", nc.PREFLIGHT_BLOCKED),
    (302, {"Location": "https://example.com/"}, "", nc.PREFLIGHT_BLOCKED),
    (502, {}, "", nc.PREFLIGHT_TRANSIENT),
    (200, {}, "Cloudflare Access", nc.PREFLIGHT_CF_AUTH),
    (200, {}, "<html>ログイン</html>", nc.PREFLIGHT_NEEDS_LOGIN),
])
def test_classify(code, headers, text, expected):
    assert nc.classify_preflight_response(code, headers, text).status == expected


class _Resp:
    def __init__(self, code, headers=None, text=""):
        self.status_code = code
        self.headers = headers or {}
        self.text = text


class _Sess:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0
        self.proxies = {}

    def get(self, url, **k):
        assert k.get("allow_redirects") is False, "リダイレクトを追うと転送先で判定できない"
        self.calls += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    def post(self, *a, **k):
        raise AssertionError("入稿前チェックは POST しない")


def _client(seq, proxy="socks5h://127.0.0.1:18080", propose_only=False):
    c = nc.NetkeirinClient.__new__(nc.NetkeirinClient)
    c.propose_only = propose_only
    c.proxy_url = proxy
    c.session = _Sess(seq)
    return c


def test_proxy_error_is_egress_down():
    c = _client([requests.exceptions.ProxyError("refused")])
    assert c.preflight(retries=2, wait_sec=0).status == nc.PREFLIGHT_EGRESS_DOWN
    assert c.session.calls == 1, "出口断は再試行しない"


def test_connection_error_via_proxy_is_egress_down():
    c = _client([requests.exceptions.ConnectionError("x")])
    assert c.preflight_once().status == nc.PREFLIGHT_EGRESS_DOWN


def test_connection_error_without_proxy_is_transient():
    c = _client([requests.exceptions.ConnectionError("x")], proxy="")
    assert c.preflight_once().status == nc.PREFLIGHT_TRANSIENT


def test_cf_auth_is_not_retried():
    c = _client([_Resp(403, {"Server": "cloudflare"})] * 3)
    assert c.preflight(retries=2, wait_sec=0).status == nc.PREFLIGHT_CF_AUTH
    assert c.session.calls == 1


def test_transient_is_retried_then_recovers():
    c = _client([requests.exceptions.Timeout("t"), _Resp(502),
                 _Resp(200, {}, "ログアウト")])
    assert c.preflight(retries=2, wait_sec=0).ok
    assert c.session.calls == 3


@pytest.mark.real_preflight
def test_ensure_ready_logs_in_on_session_expiry(monkeypatch):
    c = _client([_Resp(302, {"Location": ".../bettool/auth/login.html"}),
                 _Resp(200, {}, "ログアウト")])
    logged = []
    monkeypatch.setattr(c, "login", lambda: logged.append(1) or True)
    assert c.ensure_ready().ok
    assert logged == [1]


@pytest.mark.real_preflight
def test_ensure_ready_reports_cf_when_login_fails(monkeypatch):
    c = _client([_Resp(302, {"Location": ".../bettool/auth/login.html"}),
                 _Resp(403, {"Server": "cloudflare"})])
    monkeypatch.setattr(c, "login", lambda: False)
    assert c.ensure_ready().status == nc.PREFLIGHT_CF_AUTH


# ── 3. 承認制は通信しない ──────────────────────────────────────────────

class _Boom:
    def get(self, *a, **k):
        raise AssertionError("承認制なのに GET しました")

    def post(self, *a, **k):
        raise AssertionError("承認制なのに POST しました")


def _propose_client(monkeypatch, cache: dict):
    c = nc.NetkeirinClient.__new__(nc.NetkeirinClient)
    c.propose_only = True
    c.proxy_url = ""
    c.session = _Boom()
    monkeypatch.setattr(c, "_load_venue_cache", lambda: dict(cache))
    return c


_LEG = nc.BetLeg(nc.BET_KIND_TRIFECTA_FORMATION, [[1], [4], [5]], 1000)


def test_propose_only_never_touches_network(monkeypatch):
    from datetime import date
    c = _propose_client(monkeypatch, {"松阪": "47"})
    ok, msg = c.submit_pick_multi(
        race_date=date(2026, 11, 11), venue_name="松阪", race_no=5, n_cars=7,
        legs=[_LEG], marks={1: "◎"}, title="t")
    assert ok and msg.startswith(nc.PROPOSED_PREFIX)


def test_propose_only_unknown_venue_fails_cleanly(monkeypatch):
    from datetime import date
    c = _propose_client(monkeypatch, {})
    ok, msg = c.submit_pick_multi(
        race_date=date(2026, 11, 11), venue_name="未知", race_no=5, n_cars=7,
        legs=[_LEG], marks={1: "◎"}, title="t")
    assert not ok and "場コード" in msg


# ── 4・5. 型ラボの入稿スクリプト ──────────────────────────────────────

_ROW = {
    "race_key": "20261111_47_05", "race_date": "2026-11-11", "venue_name": "松阪",
    "race_no": 5, "race_type": "予選", "n_entries": 7, "cup_grade": None,
    "type_label": "A", "axis_sum": 1.9, "axis1": 1, "axis2": 4,
    "p3_order": "1-4-5-6-7-2-3", "plan_key": "A_hit", "bet_type": "trifecta",
    "n_legs": 1, "budget": 10_000, "pred_mean_payout": 30_000.0,
    "pred_min_payout": 30_000.0, "start_at": "1762839000",
    "legs": [{"combo": "1-4-5", "stake": 10_000, "pred_odds": 3.0}],
}


def _setup_run(monkeypatch, m, status: str):
    monkeypatch.setattr(m, "_load_settings", lambda: {})
    monkeypatch.setattr(m, "_approval_required", lambda: False)
    monkeypatch.setattr(m, "_load_closed_races", lambda day: set())
    monkeypatch.setattr(m, "_already_submitted", lambda keys: set())
    monkeypatch.setattr(m, "_missing_market_inputs", lambda rk: None)
    monkeypatch.setattr(m, "_build_entry_table", lambda rk, marks: None)
    monkeypatch.setattr(m, "_load_rows", lambda day: [dict(_ROW)])
    monkeypatch.setattr(m, "auto_publish_submitted", lambda dry: [])
    monkeypatch.setattr(m, "_write_confident", lambda *a, **k: None)
    monkeypatch.setattr(m, "_skip", lambda *a, **k: None)

    class _Fake:
        def __init__(self, *a, **k):
            pass

        def ensure_ready(self):
            return nc.PreflightResult(status, "test")

        def submit_pick_multi(self, **k):
            return False, "入稿失敗: test"

    monkeypatch.setattr(m, "NetkeirinClient", _Fake)
    sent: list[tuple] = []
    monkeypatch.setattr(m, "send", lambda msg, channel: sent.append((channel, msg)))
    return sent


@pytest.mark.parametrize("status", [nc.PREFLIGHT_CF_AUTH, nc.PREFLIGHT_EGRESS_DOWN,
                                    nc.PREFLIGHT_BLOCKED])
def test_run_stops_and_notifies_when_blocked(monkeypatch, status):
    from scripts import netkeirin_submit_type_lab as m
    sent = _setup_run(monkeypatch, m, status)
    submitted: list = []
    monkeypatch.setattr(m, "submit_row", lambda *a, **k: submitted.append(a) or (True, "t"))
    with pytest.raises(SystemExit) as e:
        m.run("2026-11-11", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert e.value.code == m.EXIT_PREFLIGHT_BLOCKED
    assert submitted == [], "入れないのに入稿へ進んだ"
    assert sent and sent[0][0] == "system"
    assert "松阪5R" in sent[0][1], "未入稿のレースが通知に無い"
    assert "締切" in sent[0][1]


def test_run_notifies_when_every_submission_fails(monkeypatch):
    from scripts import netkeirin_submit_type_lab as m
    sent = _setup_run(monkeypatch, m, nc.PREFLIGHT_OK)
    monkeypatch.setattr(m, "_passes_axis_gate", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(m, "submit_row", lambda *a, **k: (False, "入稿失敗"))
    m.run("2026-11-11", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert any("失敗" in msg for _, msg in sent), "全件失敗なのに通知が出ていない"


def test_run_skips_preflight_in_approval_mode(monkeypatch):
    """承認制は netkeirin に触らないので、入稿前チェックも見ない。"""
    from scripts import netkeirin_submit_type_lab as m
    _setup_run(monkeypatch, m, nc.PREFLIGHT_EGRESS_DOWN)
    monkeypatch.setattr(m, "_approval_required", lambda: True)
    monkeypatch.setattr(m, "submit_row", lambda *a, **k: (True, "t"))
    m.run("2026-11-11", "morning", dry_run=False, only_key=None, do_rebuild=False)


def test_pending_lines_excludes_done_and_closed():
    from scripts import netkeirin_submit_type_lab as m
    rows = [dict(_ROW),
            dict(_ROW, race_key="A", race_no=1, start_at="1762830000"),
            dict(_ROW, race_key="B", race_no=2)]
    lines = m._pending_lines(rows, already={("A", "A_hit")}, closed={"B"})
    assert len(lines) == 1 and lines[0].startswith("松阪5R")


# ── 死活確認ジョブの通知判定 ─────────────────────────────────────────

def test_egress_monitor_notifies_on_transitions_only():
    from scripts import check_netkeirin_egress as ce
    t0 = datetime(2026, 11, 17, 2, 0)
    ok = nc.PreflightResult(nc.PREFLIGHT_OK, "ok")
    cf = nc.PreflightResult(nc.PREFLIGHT_CF_AUTH, "cf")

    st, msg = ce.decide({}, ok, t0, 3)
    assert msg is None
    st, msg = ce.decide(st, cf, t0 + timedelta(minutes=10), 3)
    assert msg and "cf_auth" in msg
    st, msg = ce.decide(st, cf, t0 + timedelta(minutes=20), 3)
    assert msg is None, "同じ異常を10分ごとに鳴らしている"
    st, msg = ce.decide(st, cf, t0 + timedelta(hours=3, minutes=15), 3)
    assert msg and "継続中" in msg
    st, msg = ce.decide(st, ok, t0 + timedelta(hours=4), 3)
    assert msg and "再び" in msg


def test_egress_monitor_needs_login_is_healthy():
    from scripts import check_netkeirin_egress as ce
    res = nc.PreflightResult(nc.PREFLIGHT_NEEDS_LOGIN, "x")
    _, msg = ce.decide({}, res, datetime(2026, 11, 17), 3)
    assert msg is None


def test_egress_monitor_transient_needs_streak():
    from scripts import check_netkeirin_egress as ce
    tr = nc.PreflightResult(nc.PREFLIGHT_TRANSIENT, "t")
    t = datetime(2026, 11, 17)
    st, msg = ce.decide({}, tr, t, 3)
    assert msg is None
    st, msg = ce.decide(st, tr, t + timedelta(minutes=10), 3)
    assert msg is None
    st, msg = ce.decide(st, tr, t + timedelta(minutes=20), 3)
    assert msg is not None
