"""NVRTOpen が失敗したときも NVClose することのテスト。

UmaConn は NVRTOpen が負の値を返した後に NVClose しないと、次の NVRTOpen /
NVOpen を rc=-202 で拒否する（2026-10-08 実機確認: 門別1R=-1 の直後に
笠松1R を開くと -202、間に NVClose を挟むと 0）。

閉じ忘れていた頃は、配信側が一部の場だけ -1 を返す日に、その場がキー順の
先頭にあるだけで後続の全場の成績・オッズが取れなくなっていた。

    python3 -m pytest windows-agent/tests/test_umaconn_rtopen_close.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import umaconn_agent as ua  # noqa: E402


class _FakeNV:
    """実機で観測した「開きっぱなしだと次が -202」を再現するスタブ。"""

    def __init__(self, unavailable: set[str]) -> None:
        self._unavailable = unavailable
        self._open = False
        self.close_calls = 0

    def NVRTOpen(self, dataspec: str, key: str) -> int:  # noqa: N802
        if self._open:
            return -202
        self._open = True
        return -1 if key in self._unavailable else 0

    def NVRead(self, *_args: object) -> tuple[int, str, str]:  # noqa: N802
        return (0, "", "")

    def NVClose(self) -> None:  # noqa: N802
        self._open = False
        self.close_calls += 1


def test_failed_rtopen_is_closed() -> None:
    nv = _FakeNV(unavailable={"BAD"})
    assert ua.fetch_realtime_data(nv, "0B12", "BAD") == []
    assert nv.close_calls == 1


def test_failure_does_not_poison_following_keys() -> None:
    """失敗したキーの後ろにあるキーも開けること。"""
    nv = _FakeNV(unavailable={"BAD"})
    ua.fetch_realtime_data(nv, "0B12", "BAD")
    assert nv.NVRTOpen("0B12", "GOOD") == 0


def test_close_failure_is_swallowed() -> None:
    """NVClose が投げてもポーリングループを止めない。"""

    class _Raising(_FakeNV):
        def NVClose(self) -> None:  # noqa: N802
            raise OSError("COM 側の例外")

    assert ua.fetch_realtime_data(_Raising(unavailable={"BAD"}), "0B31", "BAD") == []
