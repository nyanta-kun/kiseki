"""netkeiba のログイン済みセッション。

sekito `lib/sekito/scrapers/odds/login.py` からの移設。

## 移設で直したこと: 確認ページの文字コード

移設元は確認用に取得する `race.netkeiba.com/top/` を
`r.content.decode("euc-jp", errors="replace")` で読んでいた。**このホストは UTF-8**
なので日本語が全て壊れ、「ログアウト」リンクの有無を見る判定は**常に外れていた**
（sekito@827cdb3 の文字コード修正がこのファイルには届いていなかった）。
実質のゲートは `memberRank` の正規表現だけで、たまたま ASCII なので機能していた。

移植版は `decode_page()` を使う。判定は移設元と同じ 2 段構えのまま:

    1. `'memberRank': 'NotLogin'` が入っていたら失敗
    2. 「ログアウト」が無ければ `memberRank` を正規表現で拾い、
       取れないか NotLogin なら失敗

つまり**この修正で判定が緩くなることはない**。壊れていた 1 段目が効くようになるだけ。

## 環境変数名の違い

移設元は `NETKEIBA_ID` / `NETKEIBA_PASS` を直接読んでいた。kiseki は
`config.settings` の `netkeiba_user_id` / `netkeiba_password`
（`.env` の `NETKEIBA_USER_ID` / `NETKEIBA_PASSWORD`）を使う。
VPS の kiseki には移設時点で既に両方入っている（2026-09-06 確認）。

## User-Agent

移設元は `user_agent_manager` から毎回ランダムに選んでいた。同じ振る舞いを
`USER_AGENTS` からの選択で持つ。UA を固定すると足跡が揃って目立つため。
"""

from __future__ import annotations

import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

from .decode import decode_page

logger = logging.getLogger(__name__)

LOGIN_URL = "https://regist.netkeiba.com/account/?pid=login"
CHECK_URL = "https://race.netkeiba.com/top/"

# 実在するデスクトップ Chrome / Safari / Firefox の UA。
# 移設元 `user_agent_manager` が返していたのと同じ範囲を、依存を増やさず持つ。
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
)

MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 5

# プロキシ起因とみなすエラー片。移設元の一覧をそのまま持つ。
_PROXY_ERROR_MARKERS = ("ERR_PROXY", "ERR_TUNNEL", "ERR_CONNECTION",
                        "ConnectionError", "ProxyError")


class NetkeibaLoginError(RuntimeError):
    """ログインに失敗した。"""


class ProxyLoginError(NetkeibaLoginError):
    """プロキシ起因とみなせるログインエラー（リトライしても解消しない類）。"""


def random_user_agent() -> str:
    """UA を 1 つ選ぶ。"""
    return random.choice(USER_AGENTS)


def login(
    user_id: str,
    password: str,
    *,
    rate_limiter=None,
    user_agent: str | None = None,
) -> requests.Session:
    """netkeiba にログインして認証 cookie を持つ `requests.Session` を返す。

    Args:
        user_id: netkeiba のログイン ID。
        password: パスワード。
        rate_limiter: `.wait()` を持つオブジェクト（任意）。ログインも 1 リクエストなので
            レート制限の対象に含める。
        user_agent: 上書きしたい場合。省略時はランダム。

    Raises:
        ProxyLoginError: プロキシ起因とみなせるエラー。
        NetkeibaLoginError: その他のログイン失敗。
    """
    if not user_id or not password:
        raise NetkeibaLoginError(
            "netkeiba の認証情報が設定されていません "
            "(.env の NETKEIBA_USER_ID / NETKEIBA_PASSWORD)"
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent or random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.5",
    })

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            if rate_limiter is not None:
                rate_limiter.wait()
            _do_login(session, user_id, password)
            logger.info("netkeiba ログイン成功")
            return session
        except requests.Timeout as e:
            last_error = e
            logger.warning("netkeiba ログインがタイムアウト (%d/%d)", attempt + 1, MAX_RETRIES)
            if attempt == MAX_RETRIES - 1:
                raise ProxyLoginError("netkeiba のログインがタイムアウトしました") from e
        except Exception as e:
            last_error = e
            text_ = str(e)
            if any(m in text_ for m in _PROXY_ERROR_MARKERS):
                logger.warning("netkeiba ログイン %d/%d: プロキシ起因 %s",
                               attempt + 1, MAX_RETRIES, text_[:120])
                if attempt == MAX_RETRIES - 1:
                    raise ProxyLoginError(f"プロキシ起因のログインエラー: {e}") from e
            else:
                logger.warning("netkeiba ログイン %d/%d: %s", attempt + 1, MAX_RETRIES, e)
                if attempt == MAX_RETRIES - 1:
                    raise NetkeibaLoginError(str(e)) from e
        time.sleep(RETRY_WAIT_SECONDS)

    raise NetkeibaLoginError(str(last_error) if last_error else "ログインに到達しませんでした")


def _do_login(session: requests.Session, user_id: str, password: str) -> None:
    """ログイン 1 回ぶん。失敗は例外で返す。"""
    r = session.get(LOGIN_URL, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(decode_page(r), "html.parser")
    form = next((f for f in soup.find_all("form") if f.find("input", {"name": "pswd"})), None)
    if form is None:
        raise NetkeibaLoginError("netkeiba: ログインフォーム (pswd input) が見つからない")

    # hidden を含む既存の input を全部拾ってから ID / パスワードを上書きする。
    # CSRF トークンなどをフォームから引き継ぐため。
    payload = {
        inp.get("name"): (inp.get("value") or "")
        for inp in form.find_all("input")
        if inp.get("name")
    }
    payload["login_id"] = user_id
    payload["pswd"] = password

    r2 = session.post(
        form.get("action") or LOGIN_URL,
        data=payload,
        headers={"Origin": "https://regist.netkeiba.com", "Referer": LOGIN_URL},
        allow_redirects=True,
        timeout=15,
    )
    r2.raise_for_status()

    _assert_logged_in(session)


def _assert_logged_in(session: requests.Session) -> None:
    """ログインできているか確認する。

    移設元と同じ 2 段構え。ただし本文の復号を `decode_page` に直したので、
    1 段目（「ログアウト」の有無）が**初めて正しく効く**。
    """
    check = session.get(CHECK_URL, timeout=15)
    body = decode_page(check)

    if "'memberRank': 'NotLogin'" in body or '"memberRank": "NotLogin"' in body:
        raise NetkeibaLoginError("netkeiba ログイン後も memberRank=NotLogin")

    if "ログアウト" not in body:
        m = re.search(r"['\"]memberRank['\"]:\s*['\"]([^'\"]+)['\"]", body)
        if not m or m.group(1) == "NotLogin":
            raise NetkeibaLoginError("netkeiba ログイン成功を確認できず")
