"""複数プロセス共有ログのローテーションのテスト。

Windows では他プロセスが開いているファイルを rename できず、ローテーションが
PermissionError (WinError 32) になる。本プロジェクトのログは常に共有されている
（umaconn_agent.log = realtime 常駐 + 5分おきの fetch-results 等）ため、
「失敗しても1行も落とさず追記を続ける」ことが必須。

    python3 -m pytest windows-agent/tests/test_shared_log_rotation.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import link_common as lc  # noqa: E402


def _handler(tmp_path: Path, **kw) -> lc.SharedFileRotatingHandler:
    h = lc.SharedFileRotatingHandler(
        str(tmp_path / "agent.log"), maxBytes=200, backupCount=2, encoding="utf-8", **kw
    )
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


def _emit(h: logging.Handler, msg: str) -> None:
    h.emit(logging.LogRecord("t", logging.INFO, __file__, 0, msg, None, None))


def test_rotates_when_size_exceeded(tmp_path):
    """上限を超えたら世代を作る。"""
    h = _handler(tmp_path)
    try:
        for i in range(40):
            _emit(h, f"line {i} " + "x" * 40)
    finally:
        h.close()

    assert (tmp_path / "agent.log").exists()
    assert (tmp_path / "agent.log.1").exists(), "ローテーションが起きていない"


def test_keeps_logging_when_rollover_is_blocked(tmp_path, monkeypatch):
    """他プロセスがファイルを掴んでいても、例外を出さず追記を続ける。"""
    h = _handler(tmp_path)

    def _locked(*_args, **_kwargs):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(lc.logging.handlers.RotatingFileHandler, "doRollover", _locked)

    try:
        for i in range(40):
            _emit(h, f"line {i} " + "x" * 40)  # 例外が漏れたらここで落ちる
        _emit(h, "SENTINEL")
    finally:
        h.close()

    body = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert "SENTINEL" in body, "ローテーション失敗後にログが落ちている"
    assert not (tmp_path / "agent.log.1").exists()


def test_failed_rollover_is_not_retried_every_line(tmp_path, monkeypatch):
    """失敗後は間隔を空ける。毎行 rename を試すと無駄な syscall が走り続ける。"""
    h = _handler(tmp_path, retry_interval=3600.0)

    attempts = []

    def _locked(*_args, **_kwargs):
        attempts.append(1)
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(lc.logging.handlers.RotatingFileHandler, "doRollover", _locked)

    try:
        for i in range(60):
            _emit(h, f"line {i} " + "x" * 40)
    finally:
        h.close()

    assert len(attempts) == 1, f"再試行の間隔が効いていない (試行 {len(attempts)} 回)"


def test_rollover_resumes_after_lock_is_released(tmp_path, monkeypatch):
    """ロックが解けたら（間隔経過後に）ローテーションを再開する。"""
    h = _handler(tmp_path, retry_interval=0.0)

    calls = {"n": 0}
    real = lc.logging.handlers.RotatingFileHandler.doRollover

    def _flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(32, "being used by another process")
        real(self)

    monkeypatch.setattr(lc.logging.handlers.RotatingFileHandler, "doRollover", _flaky)

    try:
        for i in range(80):
            _emit(h, f"line {i} " + "x" * 40)
    finally:
        h.close()

    assert calls["n"] >= 2, "失敗後に再試行していない"
    assert (tmp_path / "agent.log.1").exists(), "ロック解放後もローテーションしていない"
