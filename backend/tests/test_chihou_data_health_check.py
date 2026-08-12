"""chihou データ健全性チェックの判定ロジック検査（DB接続不要）。

2026-06-08 に netkeiba の地方タイム指数が「日次 → 月曜だけ」に縮退したが、
当時の実装は **kichiuma OR netkeiba** を1本の供給率にまとめており、
kichiuma が健全な限り WARN を出せなかった。さらに比率ベースの判定は
baseline も一緒に劣化するため、崩れきった後は永久に OK を返す。

本テストはその2つの穴（供給元の合算・比率のみの判定）が塞がれたままであることを固定する。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "chihou_data_health_check.py"


def _load_module() -> Any:
    """スクリプトを直接ロードする（scripts/ はパッケージではないため）。"""
    spec = importlib.util.spec_from_file_location("chihou_data_health_check", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hc = _load_module()


class _StubCursor:
    """execute() を無視し、あらかじめ渡した行を順に返すカーソル。"""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = list(rows)
        self.executed: list[str] = []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append(sql)

    def fetchone(self) -> tuple:
        return self._rows.pop(0)


class TestExternalIndexCoverageIsPerSource:
    """供給元ごとに独立判定されること（OR で合算しないこと）。"""

    def test_netkeiba_death_warns_even_if_kichiuma_healthy(self) -> None:
        """kichiuma が満点でも netkeiba が落ちれば WARN になる。

        合算実装ではここが OK に化けていた。
        """
        # (kc_covered, nk_covered, total) を recent / baseline の順に返す
        cur = _StubCursor([
            (950, 50, 1000),    # recent  : kichiuma 95% / netkeiba 5%
            (950, 950, 1000),   # baseline: 両方 95%
        ])
        assert hc.check_external_index_coverage(cur, "20260806", "20260813", "20260714", "20260805") is False

    def test_both_healthy_is_ok(self) -> None:
        cur = _StubCursor([
            (950, 940, 1000),
            (950, 950, 1000),
        ])
        assert hc.check_external_index_coverage(cur, "20260806", "20260813", "20260714", "20260805") is True

    def test_kichiuma_death_warns_even_if_netkeiba_healthy(self) -> None:
        """逆向きも同様に検知すること。"""
        cur = _StubCursor([
            (50, 950, 1000),
            (950, 950, 1000),
        ])
        assert hc.check_external_index_coverage(cur, "20260806", "20260813", "20260714", "20260805") is False


class TestExternalIndexActiveDays:
    """「取得日数」ベースの検査。比率ベースが取りこぼす段階的縮退を捕まえる。"""

    def test_weekly_degradation_warns(self) -> None:
        """開催8日に対し netkeiba が1日しか無ければ WARN（2026-08 の実測と同じ形）。"""
        cur = _StubCursor([(8, 8, 1)])  # race_days, kc_days, nk_days
        assert hc.check_external_index_active_days(cur, "20260806", "20260813") is False

    def test_full_coverage_ok(self) -> None:
        cur = _StubCursor([(8, 8, 8)])
        assert hc.check_external_index_active_days(cur, "20260806", "20260813") is True

    def test_single_miss_tolerated(self) -> None:
        """単発の取りこぼし（8日中7日=87.5%）は閾値80%以上なので許容する。"""
        cur = _StubCursor([(8, 8, 7)])
        assert hc.check_external_index_active_days(cur, "20260806", "20260813") is True

    def test_no_race_days_is_ok(self) -> None:
        """開催が無い期間は判定不能なので OK 扱い（ゼロ除算もしない）。"""
        cur = _StubCursor([(0, 0, 0)])
        assert hc.check_external_index_active_days(cur, "20260806", "20260813") is True

    @pytest.mark.parametrize("threshold", [hc.ACTIVE_DAYS_MIN_RATIO])
    def test_threshold_is_meaningful(self, threshold: float) -> None:
        """閾値が 0（常にOK）や 1（1日でも欠けたらWARN）に退化していないこと。"""
        assert 0.5 < threshold < 1.0
