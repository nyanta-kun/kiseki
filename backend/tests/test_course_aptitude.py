"""コース適性指数算出 ユニットテスト

DB接続不要のモックベーステスト。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.indices.course_aptitude import MIN_SAMPLE, CourseAptitudeCalculator
from src.utils.constants import SPEED_INDEX_MEAN

# ---------------------------------------------------------------------------
# ヘルパー: テスト用モックオブジェクト生成
# ---------------------------------------------------------------------------


def _make_race_result(
    horse_id: int = 1,
    finish_position: int = 1,
    finish_time: float | None = 93.0,
    abnormality_code: int = 0,
) -> MagicMock:
    """RaceResult モックを生成する。"""
    r = MagicMock()
    r.horse_id = horse_id
    r.finish_position = finish_position
    r.finish_time = Decimal(str(finish_time)) if finish_time is not None else None
    r.abnormality_code = abnormality_code
    return r


def _make_race(
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    condition: str = "良",
    head_count: int = 16,
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = 1
    r.course = course
    r.distance = distance
    r.surface = surface
    r.condition = condition
    r.head_count = head_count
    r.date = "20260322"
    return r


def _make_row(
    horse_id: int = 1,
    finish_position: int = 1,
    finish_time: float | None = 93.0,
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    abnormality_code: int = 0,
) -> MagicMock:
    """(RaceResult, Race) のタプル風モックを生成する。"""
    row = MagicMock()
    row.RaceResult = _make_race_result(horse_id, finish_position, finish_time, abnormality_code)
    row.Race = _make_race(course, distance, surface)
    return row


def _make_course_feature(
    course: str,
    direction: str = "右",
    straight_distance: float = 400.0,
    elevation_diff: float = 1.5,
    circuit_length: float = 1800.0,
    grass_type: str = "野芝",
) -> MagicMock:
    """RacecourseFeatures モックを生成する。"""
    f = MagicMock()
    f.course = course
    f.direction = direction
    f.straight_distance = straight_distance
    f.elevation_diff = elevation_diff
    f.circuit_length = circuit_length
    f.grass_type = grass_type
    return f


def _make_calculator(
    rows: list[MagicMock] | None = None,
    target_race: MagicMock | None = None,
) -> CourseAptitudeCalculator:
    """テスト用 CourseAptitudeCalculator を返す。

    _get_past_results_for_horse / _get_past_results_batch をモックし、
    DBアクセスなしで動作する。
    """
    db = AsyncMock()

    if target_race is None:
        target_race = _make_race()

    # db.execute() → Race query
    mock_race_result = MagicMock()
    mock_race_result.scalar_one_or_none.return_value = target_race
    db.execute.return_value = mock_race_result

    calc = CourseAptitudeCalculator(db=db)

    # 基準タイムキャッシュを注入（DB不要）
    calc._std_time_cache[("05", 1600, "芝")] = (93.0, 2.0)

    # コース特徴キャッシュを注入（DB不要）
    from types import SimpleNamespace

    calc._course_features = {
        "05": SimpleNamespace(
            direction="左",
            straight_distance=525.9,
            elevation_diff=2.0,
            circuit_length=2083.0,
            grass_type="野芝",
        ),
    }

    if rows is not None:
        calc._get_past_results_for_horse = AsyncMock(return_value=rows)

    return calc


# ---------------------------------------------------------------------------
# _compute_aptitude_index のユニットテスト
# ---------------------------------------------------------------------------


class TestComputeAptitudeIndex:
    """_compute_aptitude_index のテスト。"""

    def test_empty_rows_returns_mean(self) -> None:
        """過去データが0件 → None を返す（composite側でrace平均に補完）。"""
        calc = _make_calculator()
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index([], target_race)
        assert result is None

    def test_insufficient_sample_returns_mean(self) -> None:
        """MIN_SAMPLE 未満（同コースの行が少ない）→ None を返す（composite側でrace平均に補完）。"""
        calc = _make_calculator()
        # 同コース一致行が MIN_SAMPLE - 1 件
        rows = [
            _make_row(finish_position=1, course="05", distance=1600, surface="芝")
            for _ in range(MIN_SAMPLE - 1)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index(rows, target_race)
        assert result is None

    def test_got_aptitude_horse_above_mean(self) -> None:
        """得意コース（1着が多い）→ 50より高い指数を返す。"""
        calc = _make_calculator()
        # 全レース1着（速いタイム）
        rows = [
            _make_row(finish_position=1, finish_time=91.0, course="05", distance=1600, surface="芝")
            for _ in range(MIN_SAMPLE + 2)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index(rows, target_race)
        assert result > SPEED_INDEX_MEAN

    def test_poor_aptitude_horse_below_mean(self) -> None:
        """苦手コース（下位着順が多い）→ 50より低い指数を返す。"""
        calc = _make_calculator()
        # 全レース最下位付近（遅いタイム）
        rows = [
            _make_row(
                finish_position=14, finish_time=98.0, course="05", distance=1600, surface="芝"
            )
            for _ in range(MIN_SAMPLE + 2)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index(rows, target_race)
        assert result < SPEED_INDEX_MEAN

    def test_different_course_rows_ignored(self) -> None:
        """他コースのみのデータ → 重みなし → None を返す（composite側でrace平均に補完）。"""
        calc = _make_calculator()
        # 阪神(09)のデータのみ
        rows = [
            _make_row(finish_position=1, course="09", distance=1600, surface="芝")
            for _ in range(MIN_SAMPLE + 5)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index(rows, target_race)
        assert result is None

    def test_winner_gets_higher_than_loser(self) -> None:
        """同コースで1着が多い馬の方が、下位が多い馬より高い指数になる。"""
        calc_winner = _make_calculator()
        calc_loser = _make_calculator()

        rows_winner = [
            _make_row(finish_position=1, finish_time=91.0, course="05", distance=1600, surface="芝")
            for _ in range(MIN_SAMPLE + 3)
        ]
        rows_loser = [
            _make_row(
                finish_position=10, finish_time=96.0, course="05", distance=1600, surface="芝"
            )
            for _ in range(MIN_SAMPLE + 3)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")

        score_winner = calc_winner._compute_aptitude_index(rows_winner, target_race)
        score_loser = calc_loser._compute_aptitude_index(rows_loser, target_race)
        assert score_winner > score_loser

    def test_index_within_valid_range(self) -> None:
        """算出された指数が [0, 100] の範囲内に収まる。"""
        calc = _make_calculator()
        rows = [
            _make_row(finish_position=1, finish_time=70.0, course="05", distance=1600, surface="芝")
            for _ in range(MIN_SAMPLE + 5)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index(rows, target_race)
        assert 0.0 <= result <= 100.0

    def test_approximate_distance_match_is_included(self) -> None:
        """距離近似（±200m）のデータも集計に含まれる。"""
        calc = _make_calculator()
        # 1800m（1600mから+200m）の同コース・同馬場データ
        rows = [
            _make_row(finish_position=1, course="05", distance=1800, surface="芝")
            for _ in range(MIN_SAMPLE + 2)
        ]
        target_race = _make_race(course="05", distance=1600, surface="芝")
        result = calc._compute_aptitude_index(rows, target_race)
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0


# ---------------------------------------------------------------------------
# calculate / calculate_batch のインターフェーステスト
# ---------------------------------------------------------------------------


class TestCalculateInterface:
    """calculate / calculate_batch のインターフェーステスト。"""

    def _build_calc(
        self,
        horse_id: int = 1,
        rows: list[MagicMock] | None = None,
    ) -> tuple[CourseAptitudeCalculator, MagicMock]:
        """モックDB付き Calculator とターゲットレースを返す。"""
        db = AsyncMock()
        target_race = _make_race(course="05", distance=1600, surface="芝")

        if rows is None:
            rows = []

        # エントリ（RaceEntry）のモック
        entry = MagicMock()
        entry.horse_id = horse_id

        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = target_race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = [entry]
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = CourseAptitudeCalculator(db=db)
        calc._std_time_cache[("05", 1600, "芝")] = (93.0, 2.0)
        calc._get_past_results_for_horse = AsyncMock(return_value=rows)
        calc._get_past_results_batch = AsyncMock(return_value={horse_id: rows})

        return calc, target_race

    async def test_calculate_no_data_returns_mean(self) -> None:
        """calculate: 過去データなし → SPEED_INDEX_MEAN。"""
        calc, _ = self._build_calc(horse_id=1, rows=[])
        result = await calc.calculate(race_id=1, horse_id=1)
        assert result == SPEED_INDEX_MEAN

    async def test_calculate_batch_returns_all_horses(self) -> None:
        """calculate_batch: エントリ全馬のhorse_idがキーとして返る。"""
        db = AsyncMock()
        target_race = _make_race()

        horse_ids = [1, 2, 3]
        entries = []
        for hid in horse_ids:
            e = MagicMock()
            e.horse_id = hid
            entries.append(e)

        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = target_race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = entries
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = CourseAptitudeCalculator(db=db)
        calc._std_time_cache[("05", 1600, "芝")] = (93.0, 2.0)
        calc._get_past_results_batch = AsyncMock(return_value={hid: [] for hid in horse_ids})

        result = await calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)

    async def test_calculate_batch_no_entry_returns_empty(self) -> None:
        """calculate_batch: エントリなし → 空dict。"""
        db = AsyncMock()
        target_race = _make_race()
        mock_race_result = MagicMock()
        mock_race_result.scalar_one_or_none.return_value = target_race
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [mock_race_result, mock_entries_result]

        calc = CourseAptitudeCalculator(db=db)
        result = await calc.calculate_batch(race_id=1)
        assert result == {}

    async def test_calculate_race_not_found_returns_mean(self) -> None:
        """calculate: レースが存在しない → SPEED_INDEX_MEAN。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        calc = CourseAptitudeCalculator(db=db)
        result = await calc.calculate(race_id=999, horse_id=1)
        assert result == SPEED_INDEX_MEAN
