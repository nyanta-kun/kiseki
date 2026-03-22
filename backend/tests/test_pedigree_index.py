"""血統指数算出 ユニットテスト

DB接続不要のユニットテストと、SQLAlchemy Session をモックした統合テスト。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.indices.pedigree import (
    BASE_SCORE,
    DAM_SIRE_WEIGHT,
    DIST_ADJ_BONUS,
    DIST_BONUS,
    NEUTRAL_SCORE,
    SIRE_WEIGHT,
    SURFACE_BONUS,
    PedigreeIndexCalculator,
    _dist_category,
    _sire_line_score,
    _surface_key,
)
from src.utils.constants import SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# ユーティリティ: _dist_category
# ---------------------------------------------------------------------------


class TestDistCategory:
    """距離カテゴリ変換テスト。"""

    def test_sprint_boundary_1400(self) -> None:
        """1400m → sprint"""
        assert _dist_category(1400) == "sprint"

    def test_sprint_short_1000(self) -> None:
        """1000m → sprint"""
        assert _dist_category(1000) == "sprint"

    def test_mile_lower_boundary_1401(self) -> None:
        """1401m → mile"""
        assert _dist_category(1401) == "mile"

    def test_mile_boundary_1800(self) -> None:
        """1800m → mile"""
        assert _dist_category(1800) == "mile"

    def test_middle_lower_boundary_1801(self) -> None:
        """1801m → middle"""
        assert _dist_category(1801) == "middle"

    def test_middle_boundary_2400(self) -> None:
        """2400m → middle"""
        assert _dist_category(2400) == "middle"

    def test_long_2401(self) -> None:
        """2401m → long"""
        assert _dist_category(2401) == "long"

    def test_long_3600(self) -> None:
        """3600m（長距離）→ long"""
        assert _dist_category(3600) == "long"


# ---------------------------------------------------------------------------
# ユーティリティ: _surface_key
# ---------------------------------------------------------------------------


class TestSurfaceKey:
    """馬場種別正規化テスト。"""

    def test_turf_starts_with_芝(self) -> None:
        """芝 → turf"""
        assert _surface_key("芝") == "turf"

    def test_turf_with_suffix(self) -> None:
        """芝（良） → turf"""
        assert _surface_key("芝（良）") == "turf"

    def test_dirt_starts_with_ダ(self) -> None:
        """ダート → dirt"""
        assert _surface_key("ダート") == "dirt"

    def test_dirt_short(self) -> None:
        """ダ → dirt"""
        assert _surface_key("ダ") == "dirt"

    def test_unknown_none(self) -> None:
        """None → unknown"""
        assert _surface_key(None) == "unknown"

    def test_unknown_obstacle(self) -> None:
        """障害 → unknown"""
        assert _surface_key("障害") == "unknown"

    def test_empty_string(self) -> None:
        """空文字 → unknown"""
        assert _surface_key("") == "unknown"


# ---------------------------------------------------------------------------
# ユーティリティ: _sire_line_score
# ---------------------------------------------------------------------------


class TestSireLineScore:
    """系統スコア算出テスト。"""

    def test_none_sire_line_returns_neutral(self) -> None:
        """sire_line=None → NEUTRAL_SCORE（50.0）"""
        score = _sire_line_score(None, "turf", "middle")
        assert score == NEUTRAL_SCORE

    def test_unknown_sire_line_returns_neutral(self) -> None:
        """sire_line="不明" → NEUTRAL_SCORE（50.0）"""
        score = _sire_line_score("不明", "turf", "middle")
        assert score == NEUTRAL_SCORE

    def test_perfect_match_turf_middle(self) -> None:
        """芝・中距離が得意な系統が芝・中距離レースで最高スコアを取る"""
        # ディープインパクト系: turf, [middle, long, mile]
        score = _sire_line_score("ディープインパクト系", "turf", "middle")
        expected = BASE_SCORE + SURFACE_BONUS + DIST_BONUS
        assert score == pytest.approx(expected)

    def test_surface_mismatch_no_surface_bonus(self) -> None:
        """surface 不一致 → SURFACE_BONUS なし"""
        # ディープインパクト系は turf。dirt でマッチしない。
        score_dirt = _sire_line_score("ディープインパクト系", "dirt", "middle")
        score_turf = _sire_line_score("ディープインパクト系", "turf", "middle")
        assert score_dirt < score_turf
        assert score_dirt == pytest.approx(BASE_SCORE + DIST_BONUS)

    def test_both_surface_gets_half_bonus(self) -> None:
        """surface="both" → SURFACE_BONUS * 0.5"""
        # キングカメハメハ系: both, [mile, middle]
        score = _sire_line_score("キングカメハメハ系", "turf", "mile")
        expected = BASE_SCORE + SURFACE_BONUS * 0.5 + DIST_BONUS
        assert score == pytest.approx(expected)

    def test_adjacent_distance_bonus(self) -> None:
        """隣接カテゴリ → DIST_ADJ_BONUS"""
        # ディープインパクト系は middle/long/mile 得意。sprint は隣接（mile隣）
        score = _sire_line_score("ディープインパクト系", "turf", "sprint")
        expected = BASE_SCORE + SURFACE_BONUS + DIST_ADJ_BONUS
        assert score == pytest.approx(expected)

    def test_no_distance_bonus_far_mismatch(self) -> None:
        """非隣接距離 → ボーナスなし"""
        # クロフネ系: dirt, [sprint, mile, middle]。long は隣接(middle)なので adj_bonus あり
        # 非隣接距離が存在しないため、別の系統で確認
        # マイバブー系: turf, [long]。sprint は long と隣接しないため adj_bonus なし
        score = _sire_line_score("マイバブー系", "turf", "sprint")
        expected = BASE_SCORE + SURFACE_BONUS  # dist bonus なし
        assert score == pytest.approx(expected)

    def test_score_capped_at_100(self) -> None:
        """スコア上限は 100.0"""
        score = _sire_line_score("ディープインパクト系", "turf", "middle")
        assert score <= 100.0

    def test_unknown_surface_gets_half_bonus(self) -> None:
        """surface=unknown → SURFACE_BONUS * 0.5（both 扱いと同等）"""
        # ディープインパクト系は turf。unknown では半額
        score = _sire_line_score("ディープインパクト系", "unknown", "middle")
        expected = BASE_SCORE + SURFACE_BONUS * 0.5 + DIST_BONUS
        assert score == pytest.approx(expected)


# ---------------------------------------------------------------------------
# テストヘルパー
# ---------------------------------------------------------------------------


def _make_mock_race(
    race_id: int,
    surface: str = "芝",
    distance: int = 2000,
) -> MagicMock:
    """テスト用 Race モックを生成する。"""
    r = MagicMock()
    r.id = race_id
    r.surface = surface
    r.distance = distance
    return r


def _make_mock_pedigree(
    horse_id: int,
    sire_line: str = "ディープインパクト系",
    dam_sire_line: str = "キングカメハメハ系",
) -> MagicMock:
    """テスト用 Pedigree モックを生成する。"""
    p = MagicMock()
    p.horse_id = horse_id
    p.sire_line = sire_line
    p.dam_sire_line = dam_sire_line
    return p


# ---------------------------------------------------------------------------
# PedigreeIndexCalculator.calculate: 単一馬テスト（モックDB）
# ---------------------------------------------------------------------------


class TestCalculateSingleHorse:
    """calculate（単一馬）のテスト。"""

    def _build_calculator(
        self,
        race: MagicMock | None,
        pedigree: MagicMock | None,
    ) -> PedigreeIndexCalculator:
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [race, pedigree]
        return PedigreeIndexCalculator(db=db)

    def test_unknown_race_returns_default(self) -> None:
        """存在しない race_id → SPEED_INDEX_MEAN（50.0）"""
        calc = self._build_calculator(race=None, pedigree=None)
        result = calc.calculate(race_id=9999, horse_id=101)
        assert result == SPEED_INDEX_MEAN

    def test_no_pedigree_returns_default(self) -> None:
        """血統データ未登録 → SPEED_INDEX_MEAN（50.0）"""
        race = _make_mock_race(1)
        calc = self._build_calculator(race=race, pedigree=None)
        result = calc.calculate(race_id=1, horse_id=101)
        assert result == SPEED_INDEX_MEAN

    def test_known_pedigree_returns_above_base(self) -> None:
        """血統データあり → BASE_SCORE より高いスコアを返す"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        pedigree = _make_mock_pedigree(101, sire_line="ディープインパクト系", dam_sire_line="キングカメハメハ系")
        calc = self._build_calculator(race=race, pedigree=pedigree)
        result = calc.calculate(race_id=1, horse_id=101)
        assert result > BASE_SCORE

    def test_result_in_range_0_to_100(self) -> None:
        """結果は常に 0-100 の範囲"""
        race = _make_mock_race(1, surface="ダート", distance=1200)
        pedigree = _make_mock_pedigree(101, sire_line="クロフネ系", dam_sire_line="フレンチデピュティ系")
        calc = self._build_calculator(race=race, pedigree=pedigree)
        result = calc.calculate(race_id=1, horse_id=101)
        assert 0.0 <= result <= 100.0

    def test_perfect_match_turf_middle(self) -> None:
        """芝・中距離で父母ともに適性あり → 高スコア"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        pedigree = _make_mock_pedigree(
            101,
            sire_line="ディープインパクト系",   # turf, [middle,long,mile]
            dam_sire_line="ハーツクライ系",       # turf, [middle,long]
        )
        calc = self._build_calculator(race=race, pedigree=pedigree)
        result = calc.calculate(race_id=1, horse_id=101)

        sire_s = BASE_SCORE + SURFACE_BONUS + DIST_BONUS
        dam_s = BASE_SCORE + SURFACE_BONUS + DIST_BONUS
        expected = round(sire_s * SIRE_WEIGHT + dam_s * DAM_SIRE_WEIGHT, 1)
        assert result == pytest.approx(expected, abs=0.2)

    def test_unknown_sire_line_uses_neutral(self) -> None:
        """不明系統 → NEUTRAL_SCORE で計算される"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        pedigree = _make_mock_pedigree(101, sire_line="不明", dam_sire_line="不明")
        calc = self._build_calculator(race=race, pedigree=pedigree)
        result = calc.calculate(race_id=1, horse_id=101)
        expected = round(NEUTRAL_SCORE * SIRE_WEIGHT + NEUTRAL_SCORE * DAM_SIRE_WEIGHT, 1)
        assert result == pytest.approx(expected, abs=0.2)


# ---------------------------------------------------------------------------
# PedigreeIndexCalculator.calculate_batch: バッチテスト（モックDB）
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch の動作テスト（DB モック使用）。"""

    def _build_batch_calc(
        self,
        race: MagicMock | None,
        entries_horse_ids: list[int],
        pedigrees: list[MagicMock],
    ) -> PedigreeIndexCalculator:
        db = MagicMock()

        # 1st call: Race query, 2nd call: RaceEntry query
        mock_race_q = MagicMock()
        mock_race_q.filter.return_value.first.return_value = race
        mock_entry_q = MagicMock()
        mock_entries = []
        for hid in entries_horse_ids:
            e = MagicMock()
            e.horse_id = hid
            mock_entries.append(e)
        mock_entry_q.filter.return_value.all.return_value = mock_entries

        # pedigrees 一括取得
        mock_ped_q = MagicMock()
        mock_ped_q.filter.return_value.all.return_value = pedigrees

        db.query.side_effect = [mock_race_q, mock_entry_q, mock_ped_q]
        return PedigreeIndexCalculator(db=db)

    def test_unknown_race_returns_empty(self) -> None:
        """存在しない race_id → 空dict"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = PedigreeIndexCalculator(db=db)
        result = calc.calculate_batch(race_id=9999)
        assert result == {}

    def test_no_entries_returns_empty(self) -> None:
        """エントリなしのレース → 空dict"""
        db = MagicMock()
        race = _make_mock_race(1)
        mock_race_q = MagicMock()
        mock_race_q.filter.return_value.first.return_value = race
        mock_entry_q = MagicMock()
        mock_entry_q.filter.return_value.all.return_value = []
        db.query.side_effect = [mock_race_q, mock_entry_q]
        calc = PedigreeIndexCalculator(db=db)
        result = calc.calculate_batch(race_id=1)
        assert result == {}

    def test_no_pedigree_returns_default(self) -> None:
        """血統未登録の馬 → SPEED_INDEX_MEAN"""
        race = _make_mock_race(1)
        calc = self._build_batch_calc(race, [101], [])
        result = calc.calculate_batch(race_id=1)
        assert result[101] == SPEED_INDEX_MEAN

    def test_all_keys_returned(self) -> None:
        """全馬の horse_id がキーとして返る"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        horse_ids = [101, 102, 103]
        pedigrees = [
            _make_mock_pedigree(hid, sire_line="ディープインパクト系", dam_sire_line="ハーツクライ系")
            for hid in horse_ids
        ]
        calc = self._build_batch_calc(race, horse_ids, pedigrees)
        result = calc.calculate_batch(race_id=1)
        assert set(result.keys()) == set(horse_ids)

    def test_aptitude_match_higher_than_mismatch(self) -> None:
        """適性一致馬 > 不一致馬 のスコア順序"""
        race = _make_mock_race(1, surface="芝", distance=2000)  # 芝・中距離

        # 101: 芝・中距離得意（適性一致）
        ped_match = _make_mock_pedigree(101, sire_line="ディープインパクト系", dam_sire_line="ハーツクライ系")
        # 102: ダート・短距離得意（適性不一致）
        ped_mismatch = _make_mock_pedigree(102, sire_line="クロフネ系", dam_sire_line="フレンチデピュティ系")

        calc = self._build_batch_calc(race, [101, 102], [ped_match, ped_mismatch])
        result = calc.calculate_batch(race_id=1)
        assert result[101] > result[102]

    def test_mixed_pedigree_missing_one(self) -> None:
        """血統あり馬とない馬が混在 → ない馬は SPEED_INDEX_MEAN"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        ped = _make_mock_pedigree(101, sire_line="ディープインパクト系", dam_sire_line="ハーツクライ系")
        calc = self._build_batch_calc(race, [101, 102], [ped])
        result = calc.calculate_batch(race_id=1)
        assert result[102] == SPEED_INDEX_MEAN
        assert result[101] != SPEED_INDEX_MEAN
