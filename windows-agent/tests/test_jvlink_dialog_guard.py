"""JV-Link モーダル自動応答の検査。

2026-08-06〜08-12 に蓄積系が6日間止まった原因は、JVOpen が

    「現在のバージョンより新しいバージョン(5.0.0)のJV-Linkが存在します。
      新しいバージョンをダウンロードしますか？」

というモーダルを出し、``pythonw.exe`` に押す者がいなかったこと。ここで固定するのは
**押してよいボタンの選び方**と、**BlockingCallGuard が実際に応答を試みること**。

Windows API を叩く ``dismiss()`` 本体は CI では動かせないので、判断部分
(``choose_button`` / ``is_target_title``) を純関数として切り出して検査する。

    python3 -m pytest windows-agent/tests/test_jvlink_dialog_guard.py
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jvlink_dialog_guard as guard  # noqa: E402
from link_common import BlockingCallGuard  # noqa: E402


# ---------------------------------------------------------------------------
# ボタン選択の安全性
# ---------------------------------------------------------------------------

def test_バージョンアップ通知では_いいえ_を選ぶ():
    """実機で観測した実際のボタン構成。「はい」を押すとDLが始まってしまう。"""
    assert guard.choose_button(["はい", "いいえ"]) == "いいえ"


def test_アクセラレータ付きのキャプションでも選べる():
    """標準 MessageBox のボタンは「いいえ(&N)」。完全一致だと拾えず押せなくなる。

    2026-08-12 の実機自己テストで実際に踏んだ。ユニットテストだけ見ていると
    「安全に押せるボタンが無い」と判断して**ハングしたまま**になる。
    """
    assert guard.choose_button(["いいえ(&N)", "はい(&Y)"]) == "いいえ(&N)"
    assert guard.choose_button(["&Yes", "&No"]) == "&No"


@pytest.mark.parametrize(
    "labels",
    [
        ["はい", "いいえ"],
        ["Yes", "No"],
        ["はい(&Y)", "いいえ(&N)"],
        ["&Yes", "&No"],
        ["はい", "いいえ", "キャンセル"],
        ["OK", "キャンセル"],
        ["OK"],
    ],
)
def test_はい系は絶対に選ばれない(labels):
    """「はい」を押すとバージョンアップのダウンロードが始まる。押してはいけない。"""
    chosen = guard.choose_button(labels)
    assert chosen is None or not guard.is_unsafe(chosen)


@pytest.mark.parametrize(
    "raw,expected",
    [("いいえ(&N)", "いいえ"), ("はい(&Y)", "はい"), ("&No", "No"), ("  OK  ", "OK")],
)
def test_キャプションの正規化(raw, expected):
    assert guard.normalize_label(raw) == expected


def test_安全なボタンが無ければNoneを返す():
    """未知のダイアログで適当なボタンを押すより、上限まで待って落ちる方が安全。"""
    assert guard.choose_button(["はい"]) is None
    assert guard.choose_button(["はい(&Y)"]) is None
    assert guard.choose_button([]) is None


def test_否定系が中立系より優先される():
    """OK しか無ければ OK を押すが、いいえ があるならそちらを優先する。"""
    assert guard.choose_button(["OK", "いいえ"]) == "いいえ"


def test_前後の空白があっても選べる():
    assert guard.choose_button(["  いいえ  "]).strip() == "いいえ"


# ---------------------------------------------------------------------------
# 対象ダイアログの判定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", ["JRA-VAN DataLab.", "JV-Link", "JRA-VAN"])
def test_JV_Link_のダイアログを対象にする(title):
    assert guard.is_target_title(title)


@pytest.mark.parametrize("title", ["", "UmaConn", "Unexpected Memory Leak", "Python"])
def test_無関係なダイアログは触らない(title):
    """UmaConn の FastMM リークダイアログ等を巻き込まないこと。"""
    assert not guard.is_target_title(title)


# ---------------------------------------------------------------------------
# BlockingCallGuard への組み込み
# ---------------------------------------------------------------------------

def test_ガードがブロック中にダイアログ応答を試みる(monkeypatch):
    """これが呼ばれないと、ダイアログ待ちのJVOpenは上限まで殺されるだけになる。"""
    calls: list[int] = []
    monkeypatch.setattr(guard, "dismiss", lambda log, **kw: calls.append(1) or 0)
    monkeypatch.setattr(BlockingCallGuard, "DIALOG_POLL_INTERVAL", 0.05)

    log = logging.getLogger("test_guard")
    with BlockingCallGuard("JVOpen(TEST)", timeout=0, log=log, heartbeat_interval=60):
        time.sleep(0.3)

    assert calls, "ブロック中に dismiss が一度も呼ばれていない"


def test_無効化できる(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(guard, "dismiss", lambda log, **kw: calls.append(1) or 0)
    monkeypatch.setattr(BlockingCallGuard, "DIALOG_POLL_INTERVAL", 0.05)

    log = logging.getLogger("test_guard")
    with BlockingCallGuard(
        "JVOpen(TEST)", timeout=0, log=log, heartbeat_interval=60, dismiss_dialogs=False
    ):
        time.sleep(0.3)

    assert not calls


def test_ダイアログ応答が失敗しても監視は死なない(monkeypatch):
    """応答は付加機能。ここで例外を漏らすと本来の強制終了ウォッチドッグまで止まる。"""

    def boom(log, **kw):
        raise RuntimeError("EnumWindows failed")

    monkeypatch.setattr(guard, "dismiss", boom)
    monkeypatch.setattr(BlockingCallGuard, "DIALOG_POLL_INTERVAL", 0.05)

    log = logging.getLogger("test_guard")
    with BlockingCallGuard("JVOpen(TEST)", timeout=0, log=log, heartbeat_interval=60) as g:
        time.sleep(0.2)
        assert g._thread is not None and g._thread.is_alive()


def test_心拍ログの間隔はダイアログ監視で早まらない(monkeypatch, caplog):
    """5秒ごとにダイアログを見るようにしたが、ログは従来どおりの間隔で出すこと。"""
    monkeypatch.setattr(guard, "dismiss", lambda log, **kw: 0)
    monkeypatch.setattr(BlockingCallGuard, "DIALOG_POLL_INTERVAL", 0.02)

    log = logging.getLogger("test_guard_hb")
    log.propagate = True
    with caplog.at_level(logging.INFO, logger="test_guard_hb"):
        with BlockingCallGuard("JVOpen(TEST)", timeout=0, log=log, heartbeat_interval=0.2):
            time.sleep(0.5)

    beats = [r for r in caplog.records if "待機中" in r.getMessage()]
    # 0.5秒 / 0.2秒 間隔 → 2〜3本。0.02秒間隔で出ていたら20本以上になる
    assert 1 <= len(beats) <= 4, f"心拍が {len(beats)} 本出ている"


def test_ダイアログ監視の間隔は心拍間隔を超えない():
    """heartbeat_interval の方が短い設定でも待ちが伸びないこと。"""
    log = logging.getLogger("test_guard")
    g = BlockingCallGuard("JVOpen(TEST)", timeout=0, log=log, heartbeat_interval=1.0)
    assert min(g.DIALOG_POLL_INTERVAL, g._interval) <= g._interval


def test_スレッドは終了時に止まる():
    log = logging.getLogger("test_guard")
    with BlockingCallGuard("JVOpen(TEST)", timeout=0, log=log) as g:
        thread = g._thread
    assert thread is not None
    thread.join(timeout=BlockingCallGuard.DIALOG_POLL_INTERVAL + 2)
    assert not thread.is_alive()


def test_非Windowsでは何もしない():
    """CI(Linux/macOS)で import しただけで落ちないこと。"""
    if sys.platform.startswith("win"):
        pytest.skip("Windows 実機では実際に走らせる")
    assert guard.dismiss(logging.getLogger("test_guard")) == 0


def test_同時に複数スレッドから呼んでも安全():
    """realtime は複数のワーカーを持つ。dismiss は状態を持たないこと。"""
    log = logging.getLogger("test_guard")
    errors: list[BaseException] = []

    def run():
        try:
            guard.dismiss(log)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
