"""JVOpen ハング監視 (BlockingCallGuard) のテスト。

JV-Link は同時1接続しか持てないため、JVOpen 内で居座ったプロセスが1本いると
他のバックフィルも realtime も全て止まる。2026-08-05 18:00 起動の
`jvlink_agent.py --mode weekly-preview` は JVOpen から 23.3 時間返らず、
4時間おきの `jvlink_historical` が丸2日間 1 ファイルも取得できなかった。

COM ブロックは呼び出しスレッドから中断できないので、別スレッドから `os._exit`
するしか回収手段がない。ここでは「上限を超えたら落とす」「超えなければ落とさない」
という契約を、`os._exit` をスタブに差し替えて検証する。

    python3 -m pytest windows-agent/tests/test_blocking_call_guard.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import link_common as lc  # noqa: E402


def _guard(timeout: float, interval: float = 0.02) -> lc.BlockingCallGuard:
    return lc.BlockingCallGuard(
        "JVOpen(RACE)", timeout, logging.getLogger("test"), heartbeat_interval=interval
    )


def test_exits_when_call_exceeds_timeout(monkeypatch):
    """上限を超えて返らない呼び出しはプロセス終了を要求する。"""
    exits: list[int] = []
    monkeypatch.setattr(lc.os, "_exit", lambda code: exits.append(code))

    with _guard(timeout=0.05):
        time.sleep(0.4)  # JVOpen がブロックしている状態を模す

    assert exits, "上限超過なのに強制終了が呼ばれていない"
    assert exits[0] == 1


def test_does_not_exit_when_call_returns_in_time(monkeypatch):
    """上限内に返った呼び出しは落とさない。"""
    exits: list[int] = []
    monkeypatch.setattr(lc.os, "_exit", lambda code: exits.append(code))

    with _guard(timeout=5.0):
        time.sleep(0.05)

    time.sleep(0.1)  # __exit__ 後に監視スレッドが起きても発火しないこと
    assert exits == []


def test_timeout_zero_disables_the_guard(monkeypatch):
    """timeout<=0 は監視無効。設定で無効化できる逃げ道を残す。"""
    exits: list[int] = []
    monkeypatch.setattr(lc.os, "_exit", lambda code: exits.append(code))

    with _guard(timeout=0):
        time.sleep(0.15)

    assert exits == []


def test_heartbeat_is_logged_while_blocked(caplog):
    """ブロック中は経過時間がログに出る（外部ウォッチドッグの手がかり）。"""
    with caplog.at_level(logging.INFO, logger="test"):
        with _guard(timeout=0, interval=0.02):
            time.sleep(0.12)

    assert any("JVOpen(RACE) 待機中" in r.message for r in caplog.records)
