"""NVLink COM オブジェクトの解放処理のテスト。

UmaConn (NVDTLab.dll) は Delphi/FastMM 製で、解放し忘れたままプロセスが終わると
「Unexpected Memory Leak」モーダルダイアログを出す。pythonw.exe には閉じる者が
いないためプロセスが終われず、UmaConn COM を掴んだまま居座る（2026-08-04 の障害）。

COM 自体は Windows でしか動かせないが、解放を「必ず1回呼ぶ」「例外でも止まらない」
という契約はスタブで検証できる。

    python3 -m pytest windows-agent/tests/test_umaconn_release.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import umaconn_agent as ua  # noqa: E402


class _FakeNV:
    """NVLink COM オブジェクトのスタブ。"""

    def __init__(self, raises: bool = False) -> None:
        self.close_calls = 0
        self._raises = raises

    def NVClose(self) -> None:  # noqa: N802  (COM 側の命名に合わせる)
        self.close_calls += 1
        if self._raises:
            raise OSError("COM オブジェクトが既に死んでいる")


def test_release_calls_nvclose_once() -> None:
    nv = _FakeNV()
    assert ua.release_nvlink(nv, "test") is None
    assert nv.close_calls == 1


def test_release_returns_none_so_caller_drops_its_reference() -> None:
    """呼び出し側が `nv = release_nvlink(nv)` と書けること。

    参照が残っていると gc されず、解放がインタプリタ終了時（＝ダイアログが出る位置）
    まで先送りされてしまう。
    """
    nv = _FakeNV()
    nv = ua.release_nvlink(nv, "test")
    assert nv is None


def test_release_survives_nvclose_failure() -> None:
    """NVClose が投げても解放処理を止めない。

    ここで例外が抜けると os._exit 前の解放が丸ごと飛び、まさに直そうとしている
    リークダイアログに戻る。
    """
    nv = _FakeNV(raises=True)
    assert ua.release_nvlink(nv, "test") is None
    assert nv.close_calls == 1


def test_release_is_idempotent_on_none() -> None:
    """二重解放（shutdown 経路 → finally 経路）で落ちないこと。"""
    assert ua.release_nvlink(None, "test") is None


@pytest.mark.parametrize("raises", [False, True])
def test_release_never_raises(raises: bool) -> None:
    ua.release_nvlink(_FakeNV(raises=raises), "test")


def test_run_mode_is_separate_from_main() -> None:
    """モード実行が main から切り出されていること。

    main 側の `try: _run_mode(...) finally: release_nvlink(...)` が成立する前提。
    ここが main に戻ると全モードで解放が失われる。
    """
    assert callable(ua._run_mode)
    import inspect
    src = inspect.getsource(ua.main)
    assert "_run_mode(args, nv)" in src
    assert "finally:" in src
    assert "release_nvlink(nv" in src
