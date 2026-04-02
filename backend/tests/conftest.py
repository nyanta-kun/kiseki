"""共通テストフィクスチャ

各テストモジュールで共通して使用するフィクスチャ・ヘルパーを定義する。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models import Race, RaceEntry, RaceResult


@pytest.fixture
def mock_session() -> AsyncMock:
    """DBセッションのモックを返す（非同期対応）。"""
    return AsyncMock()


@pytest.fixture
def make_race():
    """Raceオブジェクトのファクトリを返す。

    Usage:
        race = make_race()
        race = make_race(race_id="2026040205010105", distance=2400)
    """

    def _make_race(
        race_id: str = "2026040205010105",
        course: str = "05",
        distance: int = 1600,
        surface: str = "芝",
        condition: str = "良",
        date: str = "20260402",
        head_count: int = 16,
        race_number: int = 5,
        jravan_race_id: str | None = None,
        **kwargs,
    ) -> MagicMock:
        """Race モックを生成する。

        Args:
            race_id: レースID文字列（未使用、互換性のため残す）
            course: 競馬場コード（2桁）
            distance: 距離（m）
            surface: 馬場種別（芝/ダ/障）
            condition: 馬場状態（良/稍重/重/不良）
            date: 開催日（YYYYMMDD）
            head_count: 出走頭数
            race_number: レース番号
            jravan_race_id: JRA-VANレースID（16文字）

        Returns:
            Race の MagicMock
        """
        r = MagicMock(spec=Race)
        r.id = 1
        r.course = course
        r.distance = distance
        r.surface = surface
        r.condition = condition
        r.date = date
        r.head_count = head_count
        r.race_number = race_number
        r.jravan_race_id = jravan_race_id or f"{date}00{course}010105"
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    return _make_race


@pytest.fixture
def make_entry():
    """RaceEntryオブジェクトのファクトリを返す。

    Usage:
        entry = make_entry(horse_id=101, horse_number=1)
    """

    def _make_entry(
        horse_id: int = 101,
        horse_number: int = 1,
        frame_number: int | None = 1,
        weight_carried: float = 55.0,
        **kwargs,
    ) -> MagicMock:
        """RaceEntry モックを生成する。

        Args:
            horse_id: 馬ID
            horse_number: 馬番
            frame_number: 枠番
            weight_carried: 斤量（kg）

        Returns:
            RaceEntry の MagicMock
        """
        e = MagicMock(spec=RaceEntry)
        e.horse_id = horse_id
        e.horse_number = horse_number
        e.frame_number = frame_number
        e.weight_carried = Decimal(str(weight_carried))
        for k, v in kwargs.items():
            setattr(e, k, v)
        return e

    return _make_entry


@pytest.fixture
def make_result():
    """RaceResultオブジェクトのファクトリを返す。

    Usage:
        result = make_result(horse_id=101, finish_position=1)
    """

    def _make_result(
        horse_id: int = 101,
        finish_position: int = 1,
        finish_time: float | None = 93.0,
        last_3f: float | None = 34.0,
        abnormality_code: int = 0,
        frame_number: int | None = 1,
        passing_4: int | None = None,
        weight_change: int | None = None,
        **kwargs,
    ) -> MagicMock:
        """RaceResult モックを生成する。

        Args:
            horse_id: 馬ID
            finish_position: 着順
            finish_time: 走破タイム（秒）
            last_3f: 後半3Fタイム（秒）
            abnormality_code: 異常コード（0=正常、1以上=除外/取消）
            frame_number: 枠番
            passing_4: 4コーナー通過順
            weight_change: 前走比体重変化（kg）

        Returns:
            RaceResult の MagicMock
        """
        r = MagicMock(spec=RaceResult)
        r.horse_id = horse_id
        r.finish_position = finish_position
        r.finish_time = Decimal(str(finish_time)) if finish_time is not None else None
        r.last_3f = Decimal(str(last_3f)) if last_3f is not None else None
        r.abnormality_code = abnormality_code
        r.frame_number = frame_number
        r.passing_4 = passing_4
        r.weight_change = weight_change
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    return _make_result
