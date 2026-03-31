"""枠順バイアス指数算出 ユニットテスト

DB接続不要のモックベーステスト。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.indices.frame_bias import MIN_SAMPLE, FrameBiasCalculator, _position_score
from src.utils.constants import SPEED_INDEX_MEAN

# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race(
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    head_count: int = 16,
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = 1
    r.course = course
    r.distance = distance
    r.surface = surface
    r.head_count = head_count
    r.date = "20260322"
    return r


def _make_frame_stats(
    frame_number: int,
    avg_pos_score: float,
    win_rate: float,
    cnt: int = 20,
) -> dict[str, float]:
    """枠番統計dictを生成する。"""
    return {
        "avg_pos_score": avg_pos_score,
        "win_rate": win_rate,
        "cnt": float(cnt),
    }


def _make_calc_with_frame_stats(
    stats: dict[int, dict[str, float]],
    target_race: MagicMock | None = None,
) -> FrameBiasCalculator:
    """枠番統計をキャッシュ注入済みの FrameBiasCalculator を返す。"""
    db = MagicMock()

    if target_race is None:
        target_race = _make_race()

    db.query.return_value.filter.return_value.first.return_value = target_race

    calc = FrameBiasCalculator(db=db)
    cache_key = (target_race.course, target_race.distance, target_race.surface)
    calc._frame_stats_cache[cache_key] = stats
    return calc


# ---------------------------------------------------------------------------
# _position_score のユニットテスト
# ---------------------------------------------------------------------------


class TestPositionScore:
    """_position_score の変換テスト。"""

    def test_first_place_gets_100(self) -> None:
        """1着 → 100.0"""
        assert _position_score(1, 16) == 100.0

    def test_last_place_gets_0(self) -> None:
        """最下位 → 0.0"""
        assert _position_score(16, 16) == 0.0

    def test_middle_place(self) -> None:
        """中間着順は 0-100 の中間値になる。"""
        score = _position_score(8, 16)
        assert 0.0 < score < 100.0

    def test_single_horse_race(self) -> None:
        """1頭立て（head_count=1）は全て 100.0。"""
        assert _position_score(1, 1) == 100.0


# ---------------------------------------------------------------------------
# _compute_frame_bias のユニットテスト
# ---------------------------------------------------------------------------


class TestComputeFrameBias:
    """_compute_frame_bias のテスト。"""

    def _build_uniform_stats(self, cnt: int = 20) -> dict[int, dict[str, float]]:
        """全枠が均一な統計データを返す（全枠 50 になるべき）。"""
        return {
            frame: _make_frame_stats(frame, avg_pos_score=50.0, win_rate=0.125, cnt=cnt)
            for frame in range(1, 9)
        }

    def test_uniform_bias_returns_mean(self) -> None:
        """全枠均一な統計 → 全枠 SPEED_INDEX_MEAN を返す。"""
        stats = self._build_uniform_stats()
        calc = _make_calc_with_frame_stats(stats)
        target_race = _make_race()
        result = calc._compute_frame_bias(target_race, frame_number=1)
        assert result == pytest.approx(SPEED_INDEX_MEAN, abs=1.0)

    def test_inner_frame_advantage(self) -> None:
        """内枠有利設定: 1枠の平均着順スコアが高い → 50より高い。"""
        stats = {
            frame: _make_frame_stats(frame, avg_pos_score=50.0, win_rate=0.125)
            for frame in range(1, 9)
        }
        # 1枠を有利に設定
        stats[1] = _make_frame_stats(1, avg_pos_score=75.0, win_rate=0.25)
        calc = _make_calc_with_frame_stats(stats)
        target_race = _make_race()
        result = calc._compute_frame_bias(target_race, frame_number=1)
        assert result > SPEED_INDEX_MEAN

    def test_outer_frame_disadvantage(self) -> None:
        """外枠不利設定: 8枠の平均着順スコアが低い → 50より低い。"""
        stats = {
            frame: _make_frame_stats(frame, avg_pos_score=50.0, win_rate=0.125)
            for frame in range(1, 9)
        }
        # 8枠を不利に設定
        stats[8] = _make_frame_stats(8, avg_pos_score=25.0, win_rate=0.025)
        calc = _make_calc_with_frame_stats(stats)
        target_race = _make_race()
        result = calc._compute_frame_bias(target_race, frame_number=8)
        assert result < SPEED_INDEX_MEAN

    def test_no_stats_returns_mean(self) -> None:
        """統計データなし → SPEED_INDEX_MEAN を返す。"""
        calc = _make_calc_with_frame_stats({})
        target_race = _make_race()
        result = calc._compute_frame_bias(target_race, frame_number=1)
        assert result == SPEED_INDEX_MEAN

    def test_insufficient_sample_returns_mean(self) -> None:
        """対象枠番のサンプル数が MIN_SAMPLE 未満 → SPEED_INDEX_MEAN を返す。"""
        stats = {
            frame: _make_frame_stats(frame, avg_pos_score=50.0, win_rate=0.125)
            for frame in range(1, 9)
        }
        # 3枠だけサンプル数不足
        stats[3] = _make_frame_stats(3, avg_pos_score=80.0, win_rate=0.5, cnt=MIN_SAMPLE - 1)
        calc = _make_calc_with_frame_stats(stats)
        target_race = _make_race()
        result = calc._compute_frame_bias(target_race, frame_number=3)
        assert result == SPEED_INDEX_MEAN

    def test_index_within_valid_range(self) -> None:
        """算出された指数が [0, 100] の範囲内に収まる。"""
        stats = {
            frame: _make_frame_stats(frame, avg_pos_score=50.0, win_rate=0.125)
            for frame in range(1, 9)
        }
        stats[1] = _make_frame_stats(1, avg_pos_score=100.0, win_rate=0.9)
        calc = _make_calc_with_frame_stats(stats)
        target_race = _make_race()
        result = calc._compute_frame_bias(target_race, frame_number=1)
        assert 0.0 <= result <= 100.0

    def test_inner_higher_than_outer_in_inner_advantage_course(self) -> None:
        """内枠有利コースでは1枠 > 8枠 になる。"""
        # 内枠ほど有利なグラデーション
        stats = {
            frame: _make_frame_stats(
                frame,
                avg_pos_score=100.0 - (frame - 1) * 10.0,
                win_rate=0.25 - (frame - 1) * 0.025,
            )
            for frame in range(1, 9)
        }
        calc = _make_calc_with_frame_stats(stats)
        target_race = _make_race()
        score_inner = calc._compute_frame_bias(target_race, frame_number=1)
        score_outer = calc._compute_frame_bias(target_race, frame_number=8)
        assert score_inner > score_outer


# ---------------------------------------------------------------------------
# calculate / calculate_batch のインターフェーステスト
# ---------------------------------------------------------------------------


class TestCalculateInterface:
    """calculate / calculate_batch のインターフェーステスト。"""

    def _build_calc(
        self,
        horse_ids: list[int],
        frame_numbers: dict[int, int],
        frame_stats: dict[int, dict[str, float]] | None = None,
    ) -> FrameBiasCalculator:
        """モックDB付き Calculator を返す。

        Args:
            horse_ids: エントリ馬のhorse_idリスト
            frame_numbers: {horse_id: frame_number}
            frame_stats: 枠番統計（Noneの場合は均一統計）
        """
        db = MagicMock()
        target_race = _make_race()
        db.query.return_value.filter.return_value.first.return_value = target_race

        entries = []
        for hid in horse_ids:
            e = MagicMock()
            e.horse_id = hid
            e.frame_number = frame_numbers.get(hid)
            entries.append(e)
        db.query.return_value.filter.return_value.all.return_value = entries

        calc = FrameBiasCalculator(db=db)
        if frame_stats is None:
            frame_stats = {
                frame: _make_frame_stats(frame, avg_pos_score=50.0, win_rate=0.125)
                for frame in range(1, 9)
            }
        cache_key = (target_race.course, target_race.distance, target_race.surface)
        calc._frame_stats_cache[cache_key] = frame_stats
        return calc

    def test_calculate_batch_returns_all_horses(self) -> None:
        """calculate_batch: 全エントリ馬のhorse_idがキーとして返る。"""
        horse_ids = [101, 102, 103]
        frame_numbers = {101: 1, 102: 4, 103: 8}
        calc = self._build_calc(horse_ids, frame_numbers)
        result = calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)

    def test_calculate_batch_no_entry_returns_empty(self) -> None:
        """calculate_batch: エントリなし → 空dict。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _make_race()
        db.query.return_value.filter.return_value.all.return_value = []
        calc = FrameBiasCalculator(db=db)
        result = calc.calculate_batch(race_id=1)
        assert result == {}

    def test_calculate_race_not_found_returns_mean(self) -> None:
        """calculate: レースが存在しない → SPEED_INDEX_MEAN。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = FrameBiasCalculator(db=db)
        result = calc.calculate(race_id=999, horse_id=1)
        assert result == SPEED_INDEX_MEAN

    def test_calculate_no_frame_number_returns_mean(self) -> None:
        """calculate: 枠番なし（RaceEntryにframe_numberがない）→ SPEED_INDEX_MEAN。"""
        db = MagicMock()
        target_race = _make_race()
        # first()が2回呼ばれる: Raceの取得と、RaceEntryの取得
        entry = MagicMock()
        entry.frame_number = None

        db.query.return_value.filter.return_value.first.side_effect = [target_race, entry]

        calc = FrameBiasCalculator(db=db)
        result = calc.calculate(race_id=1, horse_id=1)
        assert result == SPEED_INDEX_MEAN

    def test_calculate_batch_no_frame_number_returns_mean(self) -> None:
        """calculate_batch: frame_numberがNoneのエントリ → SPEED_INDEX_MEAN を返す。"""
        db = MagicMock()
        target_race = _make_race()
        db.query.return_value.filter.return_value.first.return_value = target_race

        entry = MagicMock()
        entry.horse_id = 1
        entry.frame_number = None
        db.query.return_value.filter.return_value.all.return_value = [entry]

        calc = FrameBiasCalculator(db=db)
        result = calc.calculate_batch(race_id=1)
        assert result[1] == SPEED_INDEX_MEAN

    def test_relative_ordering_preserved(self) -> None:
        """内枠有利コースで 1枠馬 > 8枠馬 の順序が保たれる。"""
        horse_ids = [101, 102]
        frame_numbers = {101: 1, 102: 8}
        # 内枠有利な統計
        frame_stats = {
            frame: _make_frame_stats(
                frame,
                avg_pos_score=100.0 - (frame - 1) * 10.0,
                win_rate=0.25 - (frame - 1) * 0.025,
            )
            for frame in range(1, 9)
        }
        calc = self._build_calc(horse_ids, frame_numbers, frame_stats)
        result = calc.calculate_batch(race_id=1)
        assert result[101] > result[102]
