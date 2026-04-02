"""開催馬場バイアス ユニットテスト

MeetBiasService の get_bias / _compute_bias をテストする。
DB接続不要のモックベーステスト。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.indices.meet_bias import (
    RELIABLE_SAMPLE,
    MeetBias,
    MeetBiasService,
    _extract_kai,
    _extract_year,
)

# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race(
    course: str = "05",
    surface: str = "芝",
    date: str = "20260402",
    jravan_race_id: str | None = "2026040205010115",
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.course = course
    r.surface = surface
    r.date = date
    r.jravan_race_id = jravan_race_id
    return r


def _make_row(
    race_id: int = 1,
    frame_number: int = 1,
    finish_position: int = 1,
    passing_4: int | None = None,
    head_count: int = 16,
    abnormality_code: int = 0,
    course: str = "05",
    surface: str = "芝",
    date: str = "20260322",
    jravan_race_id: str = "2026032205010105",
) -> MagicMock:
    """(RaceResult, Race) 行モックを生成する。"""
    row = MagicMock()
    row.RaceResult.race_id = race_id
    row.RaceResult.frame_number = frame_number
    row.RaceResult.finish_position = finish_position
    row.RaceResult.passing_4 = passing_4
    row.RaceResult.abnormality_code = abnormality_code
    row.Race.id = race_id
    row.Race.course = course
    row.Race.surface = surface
    row.Race.date = date
    row.Race.head_count = head_count
    row.Race.jravan_race_id = jravan_race_id
    return row


# ---------------------------------------------------------------------------
# _extract_kai / _extract_year のユニットテスト
# ---------------------------------------------------------------------------


class TestExtractKai:
    """_extract_kai のテスト。"""

    def test_typical(self) -> None:
        """標準的なjravan_race_idからkaiを抽出できる。"""
        # "2026032209011012" → pos 8-9 (0-indexed) = "09"
        assert _extract_kai("2026032209011012") == "09"

    def test_first_kai(self) -> None:
        """第1回 (kai=01) の抽出。"""
        assert _extract_kai("2026040205010105") == "05"

    def test_none_returns_none(self) -> None:
        """Noneの場合はNoneを返す。"""
        assert _extract_kai(None) is None

    def test_short_string_returns_none(self) -> None:
        """文字列が短い場合はNoneを返す。"""
        assert _extract_kai("20260") is None


class TestExtractYear:
    """_extract_year のテスト。"""

    def test_typical(self) -> None:
        """標準的なjravan_race_idから年を抽出できる。"""
        assert _extract_year("2026032209011012") == "2026"

    def test_none_returns_none(self) -> None:
        """Noneの場合はNoneを返す。"""
        assert _extract_year(None) is None

    def test_short_string_returns_none(self) -> None:
        """文字列が短い場合はNoneを返す。"""
        assert _extract_year("202") is None


# ---------------------------------------------------------------------------
# MeetBias のユニットテスト
# ---------------------------------------------------------------------------


class TestMeetBias:
    """MeetBias データクラスのテスト。"""

    def test_default_values(self) -> None:
        """デフォルト値は全て中立（0.0）。"""
        bias = MeetBias()
        assert bias.inner_outer == 0.0
        assert bias.front_back == 0.0
        assert bias.sample_count == 0

    def test_reliability_zero_samples(self) -> None:
        """サンプル0件の場合、信頼度は0.0。"""
        bias = MeetBias(sample_count=0)
        assert bias.reliability == 0.0

    def test_reliability_full(self) -> None:
        """RELIABLE_SAMPLE以上のサンプルで信頼度1.0。"""
        bias = MeetBias(sample_count=RELIABLE_SAMPLE)
        assert bias.reliability == 1.0

    def test_reliability_half(self) -> None:
        """RELIABLE_SAMPLE/2のサンプルで信頼度0.5。"""
        bias = MeetBias(sample_count=RELIABLE_SAMPLE // 2)
        assert bias.reliability == pytest.approx(0.5)

    def test_reliability_capped_at_1(self) -> None:
        """サンプルが多くても信頼度は1.0を超えない。"""
        bias = MeetBias(sample_count=RELIABLE_SAMPLE * 10)
        assert bias.reliability == 1.0


# ---------------------------------------------------------------------------
# MeetBiasService のテスト
# ---------------------------------------------------------------------------


class TestMeetBiasServiceGetBias:
    """MeetBiasService.get_bias のテスト。"""

    async def test_no_jravan_race_id_returns_neutral(self) -> None:
        """jravan_race_id がない場合は中立バイアスを返す。"""
        db = AsyncMock()
        service = MeetBiasService(db)
        race = _make_race(jravan_race_id=None)
        bias = await service.get_bias(race)
        assert bias.inner_outer == 0.0
        assert bias.front_back == 0.0

    async def test_cache_hit_returns_same_object(self) -> None:
        """同一キーの2回目呼び出しはキャッシュから返す（DBは1回のみクエリ）。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result
        service = MeetBiasService(db)
        race = _make_race()
        bias1 = await service.get_bias(race)
        bias2 = await service.get_bias(race)
        assert bias1 is bias2
        # DBクエリは1回のみ
        assert db.execute.call_count == 1

    async def test_no_past_results_returns_neutral(self) -> None:
        """過去結果がない場合は中立バイアスを返す。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result
        service = MeetBiasService(db)
        race = _make_race()
        bias = await service.get_bias(race)
        assert bias.inner_outer == 0.0
        assert bias.front_back == 0.0
        assert bias.sample_count == 0


class TestMeetBiasServiceComputeBias:
    """MeetBiasService._compute_bias のテスト。"""

    def _make_service_with_rows(self, rows: list[MagicMock]) -> MeetBiasService:
        """モックDBに指定rows を返す MeetBiasService を生成する。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        db.execute.return_value = mock_result
        return MeetBiasService(db)

    async def test_inner_frame_advantage(self) -> None:
        """内枠(1-4番)が全勝の場合、inner_outerが正値になる。"""
        rows = []
        for i in range(RELIABLE_SAMPLE):
            # 内枠が勝ち
            row_inner = _make_row(race_id=i * 2, frame_number=1, finish_position=1)
            # 外枠は2着以下
            row_outer = _make_row(race_id=i * 2, frame_number=5, finish_position=2)
            rows.extend([row_inner, row_outer])

        service = self._make_service_with_rows(rows)
        bias = await service._compute_bias("05", "2026", "01", "20260402", "芝")
        assert bias.inner_outer > 0.0

    async def test_outer_frame_advantage(self) -> None:
        """外枠が全勝の場合、inner_outerが負値になる。"""
        rows = []
        for i in range(RELIABLE_SAMPLE):
            row_inner = _make_row(race_id=i * 2, frame_number=1, finish_position=2)
            row_outer = _make_row(race_id=i * 2, frame_number=5, finish_position=1)
            rows.extend([row_inner, row_outer])

        service = self._make_service_with_rows(rows)
        bias = await service._compute_bias("05", "2026", "01", "20260402", "芝")
        assert bias.inner_outer < 0.0

    async def test_front_bias(self) -> None:
        """4コーナー前方馬が全勝の場合、front_backが正値になる。"""
        rows = []
        for i in range(RELIABLE_SAMPLE):
            # 前走馬が1着（passing_4 が頭数の30%以内）
            row_front = _make_row(
                race_id=i * 2, frame_number=1, finish_position=1, passing_4=3, head_count=10
            )
            row_back = _make_row(
                race_id=i * 2, frame_number=5, finish_position=2, passing_4=8, head_count=10
            )
            rows.extend([row_front, row_back])

        service = self._make_service_with_rows(rows)
        bias = await service._compute_bias("05", "2026", "01", "20260402", "芝")
        assert bias.front_back > 0.0

    async def test_sample_count_matches_race_count(self) -> None:
        """sample_countが参照したレース数と一致する。"""
        rows = [
            _make_row(race_id=1, frame_number=1, finish_position=1),
            _make_row(race_id=2, frame_number=1, finish_position=1),
            _make_row(race_id=3, frame_number=1, finish_position=1),
        ]
        service = self._make_service_with_rows(rows)
        bias = await service._compute_bias("05", "2026", "01", "20260402", "芝")
        assert bias.sample_count == 3

    async def test_low_reliability_dampens_bias(self) -> None:
        """サンプル不足（1レースのみ）の場合、バイアスが減衰される。"""
        # 1レースのみ（信頼度 = 1/RELIABLE_SAMPLE）
        rows = [
            _make_row(race_id=1, frame_number=1, finish_position=1),
            _make_row(race_id=1, frame_number=5, finish_position=2),
        ]
        service = self._make_service_with_rows(rows)
        bias_low_sample = await service._compute_bias("05", "2026", "01", "20260402", "芝")

        # RELIABLE_SAMPLE件分で同じデータ
        rows_full = []
        for i in range(RELIABLE_SAMPLE):
            rows_full.append(_make_row(race_id=i * 2, frame_number=1, finish_position=1))
            rows_full.append(_make_row(race_id=i * 2, frame_number=5, finish_position=2))
        service_full = self._make_service_with_rows(rows_full)
        bias_full_sample = await service_full._compute_bias("05", "2026", "01", "20260402", "芝")

        # 少ないサンプルの方がバイアスの絶対値が小さい（0に引き寄せられる）
        assert abs(bias_low_sample.inner_outer) < abs(bias_full_sample.inner_outer)

    async def test_inner_outer_range(self) -> None:
        """inner_outer バイアスが [-1, 1] の範囲内に収まる。"""
        rows = [
            _make_row(race_id=i, frame_number=1, finish_position=1) for i in range(20)
        ]
        service = self._make_service_with_rows(rows)
        bias = await service._compute_bias("05", "2026", "01", "20260402", "芝")
        assert -1.0 <= bias.inner_outer <= 1.0
        assert -1.0 <= bias.front_back <= 1.0
