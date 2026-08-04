"""jvlink_maintenance の窓判定と rc 分類のテスト。

windows-agent は pywin32 依存のため CI では動かせないが、このモジュールは
標準ライブラリのみで完結するので単体で検証できる。

    python3 -m pytest windows-agent/tests/test_jvlink_maintenance.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jvlink_maintenance as jvm  # noqa: E402


# ---------------------------------------------------------------------------
# 窓の解析
# ---------------------------------------------------------------------------

def test_default_is_weekly_tuesday_8_to_15() -> None:
    (w,) = jvm.parse_windows(jvm.DEFAULT_MAINTENANCE_WINDOWS)
    assert w.kind == "weekly"
    assert w.weekday == 1  # 火曜
    assert (w.start.hour, w.end.hour) == (8, 15)


def test_parse_all_three_forms() -> None:
    windows = jvm.parse_windows(
        "TUE 08:00-15:00, 1ST-TUE 08:00-16:00, 2026-09-10 09:00-12:00"
    )
    assert [w.kind for w in windows] == ["weekly", "monthly", "date"]
    assert windows[1].nth == 1
    assert windows[2].on_date == date(2026, 9, 10)


def test_empty_spec_yields_no_windows() -> None:
    assert jvm.parse_windows("") == []
    assert jvm.parse_windows("  ,  ") == []


@pytest.mark.parametrize(
    "spec",
    [
        "TUE",                    # 時刻がない
        "TUE 08:00",              # 範囲になっていない
        "XYZ 08:00-15:00",        # 曜日が不正
        "9TH-TUE 08:00-15:00",    # 序数が不正
        "TUE 15:00-08:00",        # 開始 >= 終了
        "TUE 08:00-08:00",        # 幅ゼロ
    ],
)
def test_malformed_spec_raises(spec: str) -> None:
    with pytest.raises(ValueError):
        jvm.parse_windows(spec)


# ---------------------------------------------------------------------------
# 窓の判定
# ---------------------------------------------------------------------------

WEEKLY = jvm.parse_windows("TUE 08:00-15:00")

# 2026-08-04 は火曜（今回の障害当日）。2026-08-05 は水曜。
TUE = datetime(2026, 8, 4, 12, 0)
WED = datetime(2026, 8, 5, 12, 0)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 4, 7, 59), False),   # 窓の直前
        (datetime(2026, 8, 4, 8, 0), True),     # 開始ちょうどは窓の中
        (TUE, True),
        (datetime(2026, 8, 4, 11, 12), True),   # 実際に -504 が出た時刻
        (datetime(2026, 8, 4, 14, 5), True),    # 同上
        (datetime(2026, 8, 4, 14, 59), True),
        (datetime(2026, 8, 4, 15, 0), False),   # 終了ちょうどは窓の外
        (WED, False),
    ],
)
def test_weekly_window_boundaries(now: datetime, expected: bool) -> None:
    assert (jvm.active_window(now, WEEKLY) is not None) is expected


def test_monthly_window_only_matches_nth_weekday() -> None:
    windows = jvm.parse_windows("1ST-TUE 08:00-15:00")
    # 2026-08 の火曜は 4, 11, 18, 25 日
    assert jvm.active_window(datetime(2026, 8, 4, 12, 0), windows) is not None
    assert jvm.active_window(datetime(2026, 8, 11, 12, 0), windows) is None
    assert jvm.active_window(datetime(2026, 8, 25, 12, 0), windows) is None


def test_date_window_matches_single_day() -> None:
    windows = jvm.parse_windows("2026-09-10 09:00-12:00")
    assert jvm.active_window(datetime(2026, 9, 10, 10, 0), windows) is not None
    assert jvm.active_window(datetime(2026, 9, 11, 10, 0), windows) is None


def test_skip_reason_reports_remaining_minutes() -> None:
    reason = jvm.skip_reason("JVOpen(RACE)", datetime(2026, 8, 4, 14, 30), WEEKLY)
    assert reason is not None
    assert "JVOpen(RACE)" in reason
    assert "残り約30分" in reason
    assert jvm.skip_reason("JVOpen(RACE)", WED, WEEKLY) is None


def test_next_window_start_finds_following_tuesday() -> None:
    # 水曜から見た次の窓は翌週火曜 08:00
    assert jvm.next_window_start(WED, WEEKLY) == datetime(2026, 8, 11, 8, 0)
    # 窓の中から見た次の窓は「今日の残り」ではなく翌週
    assert jvm.next_window_start(TUE, WEEKLY) == datetime(2026, 8, 11, 8, 0)


def test_no_windows_configured_means_never_skip() -> None:
    assert jvm.active_window(TUE, []) is None
    assert jvm.next_window_start(TUE, []) is None


# ---------------------------------------------------------------------------
# 環境変数の読み込み
# ---------------------------------------------------------------------------

def test_load_windows_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(jvm.ENV_VAR, "WED 01:00-02:00")
    (w,) = jvm.load_windows()
    assert w.weekday == 2


def test_load_windows_falls_back_to_default_on_bad_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """誤設定で「窓なし」になるとダイアログ地獄に戻るため、既定値へ倒す。"""
    monkeypatch.setenv(jvm.ENV_VAR, "これは壊れた指定")
    windows = jvm.load_windows()
    assert [w.raw for w in windows] == [jvm.DEFAULT_MAINTENANCE_WINDOWS]


def test_load_windows_unset_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(jvm.ENV_VAR, raising=False)
    (w,) = jvm.load_windows()
    assert w.weekday == 1


# ---------------------------------------------------------------------------
# rc 分類
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("rc", "expected"),
    [
        (0, jvm.RC_OK),
        (22, jvm.RC_OK),
        (-1, jvm.RC_NO_DATA),
        (-504, jvm.RC_MAINTENANCE),   # サーバーメンテナンス中
        (-431, jvm.RC_MAINTENANCE),   # JRA-VAN サービス停止中
        (-413, jvm.RC_TRANSIENT),     # 通信確立不可
        (-411, jvm.RC_TRANSIENT),
        (-402, jvm.RC_TRANSIENT),
        (-303, jvm.RC_FATAL),         # 要復旧作業
        (-111, jvm.RC_FATAL),         # 実装バグ
        (-999, jvm.RC_FATAL),         # 未知のコードは安全側で致命扱い
    ],
)
def test_classify_rc(rc: int, expected: str) -> None:
    assert jvm.classify_rc(rc) == expected


def test_describe_rc() -> None:
    assert jvm.describe_rc(-504) == "rc=-504 (サーバーメンテナンス中)"
    assert jvm.describe_rc(-9999) == "rc=-9999"


class _RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def info(self, msg: str) -> None:
        self.calls.append(("info", msg))

    def warning(self, msg: str) -> None:
        self.calls.append(("warning", msg))

    def error(self, msg: str) -> None:
        self.calls.append(("error", msg))


@pytest.mark.parametrize(
    ("rc", "level"),
    [(-1, "info"), (-504, "warning"), (-413, "warning"), (-303, "error")],
)
def test_log_open_failure_uses_severity_by_class(rc: int, level: str) -> None:
    """-504 を ERROR で出さないこと（-303 と区別できなくなるため）。"""
    log = _RecordingLogger()
    jvm.log_open_failure(log, "JVOpen(RACE)", rc)
    assert [lvl for lvl, _ in log.calls] == [level]
