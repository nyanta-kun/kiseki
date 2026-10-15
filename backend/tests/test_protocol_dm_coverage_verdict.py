"""DM 充足率の判定（``protocol_dm_orchestrator.coverage_verdict``）を固定する。

枠順の発表前に DM を取りに行くと充足率は必ず 0% になる。それを失敗として
Discord へ流し続けていた（2026-10-10〜11・10-15）。一方で、前日・当日に
枠順が無いのは本物の障害（2026-08-16）なので、こちらは失敗のまま残す。
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "protocol_dm_orchestrator.py"


def _load_module() -> Any:
    """スクリプトを直接ロードする（scripts/ はパッケージではないため）。"""
    spec = importlib.util.spec_from_file_location("protocol_dm_orchestrator", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod() -> Any:
    """テスト対象モジュール。"""
    return _load_module()


THURSDAY = date(2026, 10, 15)


def test_frame_pending_far_ahead_is_not_failure(mod: Any) -> None:
    """木曜に日曜の出馬表（枠順なし）を見たら失敗にしない。"""
    assert mod.coverage_verdict("20261018", 17, 0, 0, THURSDAY) == "frame_pending"
    assert mod.coverage_verdict("20261017", 323, 0, 0, THURSDAY) == "frame_pending"


def test_frame_missing_day_before_or_race_day_is_failure(mod: Any) -> None:
    """前日・当日に枠順が無いのは障害（2026-08-16 型）。"""
    assert mod.coverage_verdict("20261016", 300, 0, 0, THURSDAY) == "fail"
    assert mod.coverage_verdict("20261015", 300, 0, 0, THURSDAY) == "fail"


def test_frames_present_but_dm_missing_is_failure_even_far_ahead(mod: Any) -> None:
    """枠順が入っているのに DM が無いなら、先の日でも失敗にする。"""
    assert mod.coverage_verdict("20261018", 300, 0, 300, THURSDAY) == "fail"


def test_enough_coverage_is_ok(mod: Any) -> None:
    """90% 以上は日付・枠順にかかわらず OK。"""
    assert mod.coverage_verdict("20261012", 323, 319, 321, THURSDAY) == "ok"
    assert mod.coverage_verdict("20261018", 10, 9, 0, THURSDAY) == "ok"
