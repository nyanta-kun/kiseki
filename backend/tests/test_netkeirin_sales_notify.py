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
6. **コードブロックで桁を揃えない**（2026-09-07 ユーザー指摘）。Discord の
   スマホ表示はコードブロックを折り返さず、幅の広い行が切れて崩れる
7. **「自信あり」と「的中レースの売上」を出す**（2026-09-07 ユーザー指示）。
   的中は netkeirin の表示的中と同じ `n_hits_excl_garami`（ガミを混ぜない）

⚠️ `scripts/scrape_netkeirin_sales.py` 自体は import しない。あれは `requests` /
   `psycopg2` を要求するスクリプトで、backend の CI venv には `requests` が無い
   （実際に CI がここで落ちた）。スクリプト側の配線は本文検査で固定する。
"""
from __future__ import annotations

import ast
import json
import unicodedata
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
# スマホ表示のレイアウト（2026-09-07 ユーザー指摘）
# ---------------------------------------------------------------------------

def _width(text: str) -> int:
    """全角を2桁として数えた表示幅。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def test_コードブロックを使わない():
    """🔴 Discord のスマホ表示はコードブロックを**折り返さない**。

    幅の広い行が途中で切れてレイアウトが崩れる（2026-09-07 の実スクショ）。
    通常テキストなら端末幅で自然に折り返す。
    """
    assert "```" not in rep.build_sales_message(_full_summary())


def test_1行が長くならない():
    """スマホの1行に収まる幅（全角2桁換算）に保つ。"""
    for line in rep.build_sales_message(_full_summary()).split("\n"):
        assert _width(line.replace("**", "")) <= 46, line


# ---------------------------------------------------------------------------
# 自信あり / 的中レースの売上（2026-09-07 ユーザー指示）
# ---------------------------------------------------------------------------

def _confident(**kw) -> dict:
    base = {"label": "防府4R", "rank_key": "E_hit", "n_hits_incl": 1,
            "n_hits_excl": 1, "payout": 75880, "n_sold": 18,
            "sold_paid_points": 4230}
    base.update(kw)
    return base


def _race_stats(**kw) -> dict:
    base = {"n_races": 49, "n_hit": 14, "n_hit_incl": 14, "paid": 26585,
            "paid_hit": 6220, "n_sold_hit": 29}
    base.update(kw)
    return base


def _full_summary(**kw) -> dict:
    base = {"confident": _confident(), "race_stats": _race_stats()}
    base.update(kw)
    return _summary(**base)


def test_自信ありのレースと的中と売上を出す():
    msg = rep.build_sales_message(_full_summary())
    assert "防府4R" in msg and "E_hit" in msg
    assert "的中" in msg and "75,880 円" in msg
    assert f"{rep.revenue_yen(4230):,} 円" in msg      # そのレースの売上
    assert "4,230 pt" in msg


def test_自信ありが無い日も1行出す():
    """🔴 行ごと消えると「選定が落ちた」のか「該当が無かった」のか分からない。"""
    msg = rep.build_sales_message(_summary(race_stats=_race_stats()))
    assert "🎯 **自信あり** なし" in msg


def test_自信ありのガミを的中と混ぜない():
    """netkeirin の表示的中は `n_hits_excl_garami`。払戻＜賭け金は「ガミ」。"""
    msg = rep.build_sales_message(
        _full_summary(confident=_confident(n_hits_incl=1, n_hits_excl=0,
                                           payout=8000)))
    assert "ガミ" in msg and "⭕ 的中" not in msg


def test_自信ありが採点前なら採点待ちと書く():
    """レース別を取り込んでいない回。黙って「不的中」にしない。"""
    msg = rep.build_sales_message(
        _full_summary(confident=_confident(n_hits_incl=None, n_hits_excl=None)))
    assert "採点待ち" in msg
    assert "不的中" not in msg


def test_不的中は不的中と書く():
    msg = rep.build_sales_message(
        _full_summary(confident=_confident(n_hits_incl=0, n_hits_excl=0,
                                           payout=0)))
    assert "不的中" in msg


def test_的中レースの件数と売上を出す():
    msg = rep.build_sales_message(_full_summary())
    assert "14 / 49 R" in msg
    assert "28.6%" in msg                              # 的中率
    assert f"{rep.revenue_yen(6220):,} 円" in msg      # 的中レースの売上
    assert "23.4%" in msg                              # 当日売上に占める割合


def test_ガミ込みの件数が違うときだけ併記する():
    assert "ガミ込み" not in rep.build_sales_message(_full_summary())
    msg = rep.build_sales_message(_full_summary(race_stats=_race_stats(n_hit_incl=16)))
    assert "ガミ込み 16" in msg


def test_レース別が無い回は的中レースの節を出さない():
    """🔴 0件と書くと「1本も当たらなかった」と誤読する。取り込んでいないだけ。"""
    msg = rep.build_sales_message(_summary(confident=_confident()))
    assert "的中レース" not in msg
    assert "売上" in msg                                # 本体は出す


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


def test_スクリプトが自信ありと的中レースを読む():
    """🔴 SQL を消すと**通知だけが静かに痩せる**（売上は出続ける）。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "_fetch_confident" in src and "_fetch_race_stats" in src
    # 結合キーは race_key（picks_history のランク接尾辞つきキーではない）
    assert "r.race_key = s.race_key" in src
    # 取消済みの商品は売れていない
    assert "COALESCE(s.status, 'submitted') <> 'deleted'" in src
    # 的中の定義は netkeirin の表示的中（ガミ除く）と揃える
    assert "n_hits_excl_garami" in src


def test_付加情報の失敗で通知が消えない():
    """🔴 的中・自信ありは付随情報。読めなくても売上の報告は送る。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    i = src.index("confident = _fetch_confident(")
    assert "try:" in src[max(0, i - 400):i], "付加情報が try で包まれていません"
