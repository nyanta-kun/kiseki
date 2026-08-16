"""netkeirin 売上の日次 Discord 通知（2026-08-16 追加）。

朝 9:40 の売上取り込み（`scripts/scrape_netkeirin_sales.sh`）のあとに、前日の
販売点数・販売pt・販売有償pt・売上と、当月の累計売上を送る。

## 固定すること

1. **売上は「販売*有償*pt × 0.30」**。`sold_points` には無償ptが混ざり収益に
   ならないので、そちらを掛けてはいけない。両方を並べて出す
2. **売上率の正本は1つ**。Web（`/api/keirin/netkeirin-sales`）と日次通知が
   同じモジュールを読むこと。写すと画面と Discord で売上が食い違う
3. **`keirin_sales_report` は標準ライブラリだけで書く**。VPS では取り込みが
   **keirin の venv**（FastAPI も SQLAlchemy も無い）で動くため、依存を足すと
   **Web は無事なまま通知だけが落ちる**
4. **通知の失敗で取り込みを落とさない**。売上は既に DB にあり、通知はその報告
5. **行が無い日は送らない**。0円と書くと「売れなかった」と誤読する
   （実際は開催が無いか netkeirin 側の集計待ち）

⚠️ `scripts/scrape_netkeirin_sales.py` 自体は import しない。あれは `requests` /
   `psycopg2` を要求するスクリプトで、backend の CI venv には `requests` が無い
   （実際に CI がここで落ちた）。スクリプト側の配線は本文検査で固定する。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from src.services import keirin_sales_report as rep

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPT = _BACKEND / "scripts" / "scrape_netkeirin_sales.py"
_SHELL = _BACKEND.parent / "scripts" / "scrape_netkeirin_sales.sh"


def _summary(**kw) -> dict:
    base = {
        "sale_date": "20260815", "n_sold": 85,
        "sold_points": 25500, "sold_paid_points": 10920,
        "month_n_days": 15, "month_sold_paid_points": 177015,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 売上率
# ---------------------------------------------------------------------------

def test_売上率の正本は1つ():
    """🔴 写すと画面と Discord で同じ日の売上が違う額になる。"""
    from src.api.keirin_router import NETKEIRIN_REVENUE_RATE

    assert NETKEIRIN_REVENUE_RATE is rep.REVENUE_RATE


def test_売上は有償ptから出す():
    assert rep.revenue_yen(10920) == round(10920 * rep.REVENUE_RATE)
    assert rep.revenue_yen(None) == 0
    assert rep.revenue_yen(0) == 0


# ---------------------------------------------------------------------------
# 本文
# ---------------------------------------------------------------------------

def test_販売ptと販売有償ptを両方出す():
    msg = rep.build_sales_message(_summary())
    assert "販売pt" in msg and "販売有償pt" in msg
    assert "25,500" in msg and "10,920" in msg


def test_売上に販売ptを掛けていない():
    """🔴 無償pt込みの `sold_points` を掛けると売上を過大に見せる。"""
    msg = rep.build_sales_message(_summary())
    assert f"{round(10920 * rep.REVENUE_RATE):,} 円" in msg      # 3,276円
    assert f"{round(25500 * rep.REVENUE_RATE):,} 円" not in msg  # 7,650円


def test_当月の総売上を出す():
    msg = rep.build_sales_message(_summary())
    assert f"{round(177015 * rep.REVENUE_RATE):,}" in msg        # 53,105円
    assert "累計" in msg


def test_日付は年月日で出す():
    assert "2026-08-15" in rep.build_sales_message(_summary())


def test_売上ゼロでも本文は組める():
    msg = rep.build_sales_message(
        _summary(n_sold=0, sold_points=0, sold_paid_points=0))
    assert "0 円" in msg


def test_Noneが混じっても落ちない():
    """取り込みで欠けた列があっても通知そのものは出す（欠けは数字に出る）。"""
    msg = rep.build_sales_message(
        _summary(n_sold=None, sold_points=None, sold_paid_points=None))
    assert "0 pt" in msg


# ---------------------------------------------------------------------------
# 送信
# ---------------------------------------------------------------------------

def test_URLが空なら送らない():
    assert rep.post_to_discord("", "x") is False


def test_送信に失敗しても例外を投げない(monkeypatch):
    """🔴 通知は取り込みの付随物。ここで落とすとスクレイプが失敗したように見える。"""
    def _boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(rep.urllib.request, "urlopen", _boom)
    assert rep.post_to_discord("https://example.invalid/hook", "x") is False


def test_成功したらTrueを返す(monkeypatch):
    sent: dict = {}

    class _Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = req.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(rep.urllib.request, "urlopen", _urlopen)
    assert rep.post_to_discord("https://example.invalid/hook", "こんにちは") is True
    # 本文は JSON の `content`。非ASCIIは \uXXXX へ退避される（Discord 側で復元される）
    assert json.loads(sent["body"])["content"] == "こんにちは"


# ---------------------------------------------------------------------------
# 依存の制約（破ると Web は無事なまま通知だけが落ちる）
# ---------------------------------------------------------------------------

def test_レポートモジュールは標準ライブラリだけを使う():
    """VPS では keirin の venv（FastAPI も SQLAlchemy も requests も無い）から
    読まれる。`services/keirin_marquee.py` と同じ制約。"""
    allowed = {"json", "urllib", "typing", "collections", "__future__",
               "datetime", "math"}
    tree = ast.parse(Path(rep.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for n in names:
            assert n.split(".")[0] in allowed, f"標準ライブラリ以外を import: {n}"


# ---------------------------------------------------------------------------
# スクリプト側の配線（黙って通知が消える／溢れるのを防ぐ）
# ---------------------------------------------------------------------------

def test_スクリプトは正本から文面を取る():
    """本文や売上率をスクリプトへ写していないこと。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "from src.services.keirin_sales_report import" in src
    assert "REVENUE_RATE = 0.3" not in src


def test_複数日のバックフィルでは通知しない():
    """過去分を取り直すたびに何十件も飛ぶのを防ぐ。"""
    assert "start == end" in _SCRIPT.read_text(encoding="utf-8")


def test_日別を取っていない回は通知しない():
    """売上の数字は日別テーブルにしか無い。レース別だけ取った回に送ると
    取り込んでいない日の数字を報告することになる。"""
    assert "want_day and not args.no_notify" in _SCRIPT.read_text(encoding="utf-8")


def test_シェルがwebhookを渡している():
    """🔴 python 側は env からしか URL を読めない。export を外すと
    **通知だけが静かに止まる**（取り込みは成功し続ける）。"""
    sh = _SHELL.read_text(encoding="utf-8")
    assert "export DISCORD_WEBHOOK_URL_NETKEIRIN" in sh
