"""血統指数算出 ユニットテスト（データ駆動型実装）

DB接続不要のユニットテスト。SireStatsCache をモックして適性スコア算出ロジックを検証。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.indices.pedigree import (
    DAM_SIRE_WEIGHT,
    NEUTRAL,
    SIRE_WEIGHT,
    SireStatsCache,
    PedigreeIndexCalculator,
    _CondStats,
    _dist_category,
    _surface_key,
    _weight_cat,
)
from src.utils.constants import SPEED_INDEX_MEAN


# ---------------------------------------------------------------------------
# ユーティリティ: _dist_category
# ---------------------------------------------------------------------------


class TestDistCategory:
    """距離カテゴリ変換テスト。"""

    def test_sprint_boundary_1400(self) -> None:
        assert _dist_category(1400) == "sprint"

    def test_sprint_short_1000(self) -> None:
        assert _dist_category(1000) == "sprint"

    def test_mile_lower_boundary_1401(self) -> None:
        assert _dist_category(1401) == "mile"

    def test_mile_boundary_1800(self) -> None:
        assert _dist_category(1800) == "mile"

    def test_middle_lower_boundary_1801(self) -> None:
        assert _dist_category(1801) == "middle"

    def test_middle_boundary_2400(self) -> None:
        assert _dist_category(2400) == "middle"

    def test_long_2401(self) -> None:
        assert _dist_category(2401) == "long"

    def test_long_3600(self) -> None:
        assert _dist_category(3600) == "long"

    def test_none_returns_mile(self) -> None:
        assert _dist_category(None) == "mile"


# ---------------------------------------------------------------------------
# ユーティリティ: _surface_key
# ---------------------------------------------------------------------------


class TestSurfaceKey:
    """馬場種別正規化テスト。"""

    def test_turf_starts_with_芝(self) -> None:
        assert _surface_key("芝") == "turf"

    def test_turf_with_suffix(self) -> None:
        assert _surface_key("芝（良）") == "turf"

    def test_dirt_starts_with_ダ(self) -> None:
        assert _surface_key("ダート") == "dirt"

    def test_dirt_short(self) -> None:
        assert _surface_key("ダ") == "dirt"

    def test_none_returns_dirt(self) -> None:
        """None → "dirt"（デフォルト）"""
        assert _surface_key(None) == "dirt"

    def test_other_returns_dirt(self) -> None:
        """その他 → "dirt"（デフォルト）"""
        assert _surface_key("障害") == "dirt"


# ---------------------------------------------------------------------------
# ユーティリティ: _weight_cat
# ---------------------------------------------------------------------------


class TestWeightCat:
    """斤量カテゴリ変換テスト。"""

    def test_light_55(self) -> None:
        assert _weight_cat(55.0) == "light"

    def test_light_50(self) -> None:
        assert _weight_cat(50.0) == "light"

    def test_normal_56(self) -> None:
        assert _weight_cat(56.0) == "normal"

    def test_normal_57(self) -> None:
        assert _weight_cat(57.0) == "normal"

    def test_heavy_57_5(self) -> None:
        assert _weight_cat(57.5) == "heavy"

    def test_heavy_60(self) -> None:
        assert _weight_cat(60.0) == "heavy"

    def test_none_returns_normal(self) -> None:
        assert _weight_cat(None) == "normal"


# ---------------------------------------------------------------------------
# SireStatsCache: aptitude_score テスト
# ---------------------------------------------------------------------------


def _make_cache_with_data() -> SireStatsCache:
    """テスト用にデータ入り SireStatsCache を構築する（DB不要）。"""
    db = MagicMock()
    cache = SireStatsCache(db)
    cache._loaded = True  # ロード済みとしてセット

    # 種牡馬 "TestSire" の stats を直接注入
    cache.stats = {
        "TestSire": {
            "surface": {
                "turf": _CondStats(cnt=100, win_rate=0.15, place_rate=0.45),
                "dirt": _CondStats(cnt=20, win_rate=0.05, place_rate=0.20),
            },
            "dist_cat": {
                "middle": _CondStats(cnt=80, win_rate=0.14, place_rate=0.42),
                "sprint": _CondStats(cnt=10, win_rate=0.04, place_rate=0.15),
            },
            "course": {
                "05": _CondStats(cnt=40, win_rate=0.16, place_rate=0.48),
            },
            "grass": {
                "野芝+洋芝": _CondStats(cnt=60, win_rate=0.14, place_rate=0.41),
            },
            "weight": {
                "normal": _CondStats(cnt=90, win_rate=0.14, place_rate=0.43),
            },
        },
        "WeakSire": {
            "surface": {
                "turf": _CondStats(cnt=50, win_rate=0.04, place_rate=0.15),
            },
            "dist_cat": {
                "middle": _CondStats(cnt=50, win_rate=0.04, place_rate=0.15),
            },
            "course": {},
            "grass": {},
            "weight": {},
        },
    }
    # 母集団統計
    cache.pop = {
        "surface": {
            "turf": {"mean": 0.08, "std": 0.03},
            "dirt": {"mean": 0.08, "std": 0.03},
        },
        "dist_cat": {
            "middle": {"mean": 0.08, "std": 0.03},
            "sprint": {"mean": 0.08, "std": 0.03},
        },
        "course": {
            "05": {"mean": 0.08, "std": 0.03},
        },
        "grass": {
            "野芝+洋芝": {"mean": 0.08, "std": 0.03},
        },
        "weight": {
            "normal": {"mean": 0.08, "std": 0.03},
        },
    }
    cache.course_grass = {"05": "野芝+洋芝"}
    return cache


class TestSireStatsCacheAptitudeScore:
    """SireStatsCache.aptitude_score のユニットテスト。"""

    def test_no_data_returns_neutral(self) -> None:
        """データなし種牡馬 → NEUTRAL"""
        cache = _make_cache_with_data()
        score = cache.aptitude_score("UnknownSire", "surface", "turf")
        assert score == NEUTRAL

    def test_none_name_returns_neutral(self) -> None:
        """名前なし → NEUTRAL"""
        cache = _make_cache_with_data()
        score = cache.aptitude_score(None, "surface", "turf")
        assert score == NEUTRAL

    def test_strong_sire_above_50(self) -> None:
        """得意条件（勝率 > 平均）→ 50以上のスコア"""
        cache = _make_cache_with_data()
        # TestSire の turf win_rate=0.15 > 母集団 0.08 → 高スコア
        score = cache.aptitude_score("TestSire", "surface", "turf")
        assert score > 50.0

    def test_weak_sire_below_50(self) -> None:
        """苦手条件（勝率 < 平均）→ 50以下のスコア"""
        cache = _make_cache_with_data()
        # WeakSire の turf win_rate=0.04 < 母集団 0.08 → 低スコア
        score = cache.aptitude_score("WeakSire", "surface", "turf")
        assert score < 50.0

    def test_insufficient_sample_blends_toward_neutral(self) -> None:
        """サンプル数不足 → ニュートラルへのブレンドで50に近づく"""
        cache = _make_cache_with_data()
        # dirt は cnt=20 < RELIABLE_SAMPLES=20 のボーダー（ちょうど20=1.0だが念のため）
        # sprint は cnt=10 → 信頼度=0.5 → neutral に寄る
        full_score = cache.aptitude_score("TestSire", "surface", "turf")   # cnt=100 → 信頼度高
        low_score = cache.aptitude_score("TestSire", "dist_cat", "sprint") # cnt=10 → 信頼度低
        # sprint も get_rate は低いが、サンプル不足でニュートラルに引き戻される
        # → full_score より NEUTRAL に近いはず
        assert abs(low_score - NEUTRAL) < abs(full_score - NEUTRAL)

    def test_score_in_range_0_100(self) -> None:
        """スコアは常に 0-100 の範囲"""
        cache = _make_cache_with_data()
        for sire in ["TestSire", "WeakSire", "UnknownSire"]:
            score = cache.aptitude_score(sire, "surface", "turf")
            assert 0.0 <= score <= 100.0

    def test_strong_vs_weak_ordering(self) -> None:
        """強い父 > 弱い父のスコア順序"""
        cache = _make_cache_with_data()
        strong = cache.aptitude_score("TestSire", "surface", "turf")
        weak = cache.aptitude_score("WeakSire", "surface", "turf")
        assert strong > weak


# ---------------------------------------------------------------------------
# テストヘルパー
# ---------------------------------------------------------------------------


def _make_mock_race(
    race_id: int,
    surface: str = "芝",
    distance: int = 2000,
    course: str = "05",
) -> MagicMock:
    r = MagicMock()
    r.id = race_id
    r.surface = surface
    r.distance = distance
    r.course = course
    return r


def _make_mock_pedigree(
    horse_id: int,
    sire: str | None = "TestSire",
    sire_of_dam: str | None = "WeakSire",
) -> MagicMock:
    p = MagicMock()
    p.horse_id = horse_id
    p.sire = sire
    p.sire_of_dam = sire_of_dam
    return p


def _make_mock_entry(horse_id: int, weight_carried: float = 57.0) -> MagicMock:
    e = MagicMock()
    e.horse_id = horse_id
    e.weight_carried = weight_carried
    return e


# ---------------------------------------------------------------------------
# PedigreeIndexCalculator.calculate: 単一馬テスト
# ---------------------------------------------------------------------------


class TestCalculateSingleHorse:
    """calculate（単一馬）のテスト。"""

    def _make_calc_with_cache(
        self,
        race: MagicMock | None,
        pedigree: MagicMock | None,
        entry: MagicMock | None = None,
        cache: SireStatsCache | None = None,
    ) -> PedigreeIndexCalculator:
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [race, pedigree, entry]
        calc = PedigreeIndexCalculator(db=db)
        if cache:
            calc._cache = cache
        else:
            # デフォルト: 空キャッシュ（pedigrees テーブル空）
            empty_cache = SireStatsCache(db)
            empty_cache._loaded = True
            calc._cache = empty_cache
        return calc

    def test_unknown_race_returns_default(self) -> None:
        """存在しない race_id → SPEED_INDEX_MEAN"""
        calc = self._make_calc_with_cache(race=None, pedigree=None)
        result = calc.calculate(race_id=9999, horse_id=101)
        assert result == SPEED_INDEX_MEAN

    def test_no_pedigree_returns_default(self) -> None:
        """血統データ未登録 → SPEED_INDEX_MEAN"""
        race = _make_mock_race(1)
        calc = self._make_calc_with_cache(race=race, pedigree=None)
        result = calc.calculate(race_id=1, horse_id=101)
        assert result == SPEED_INDEX_MEAN

    def test_result_in_range_0_to_100(self) -> None:
        """結果は常に 0-100"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        pedigree = _make_mock_pedigree(101)
        entry = _make_mock_entry(101)
        cache = _make_cache_with_data()
        calc = self._make_calc_with_cache(race=race, pedigree=pedigree, entry=entry, cache=cache)
        result = calc.calculate(race_id=1, horse_id=101)
        assert 0.0 <= result <= 100.0

    def test_empty_stats_returns_neutral(self) -> None:
        """キャッシュ空（pedigrees テーブル空）→ NEUTRAL(50.0)"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        pedigree = _make_mock_pedigree(101)
        calc = self._make_calc_with_cache(race=race, pedigree=pedigree)
        result = calc.calculate(race_id=1, horse_id=101)
        assert result == NEUTRAL

    def test_strong_sire_turf_middle_above_neutral(self) -> None:
        """得意条件の父 → NEUTRAL より高いスコア"""
        race = _make_mock_race(1, surface="芝", distance=2000, course="05")
        pedigree = _make_mock_pedigree(101, sire="TestSire", sire_of_dam="TestSire")
        entry = _make_mock_entry(101, weight_carried=57.0)
        cache = _make_cache_with_data()
        calc = self._make_calc_with_cache(race=race, pedigree=pedigree, entry=entry, cache=cache)
        result = calc.calculate(race_id=1, horse_id=101)
        assert result > NEUTRAL


# ---------------------------------------------------------------------------
# PedigreeIndexCalculator.calculate_batch: バッチテスト
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch の動作テスト。"""

    def _build_batch_calc(
        self,
        race: MagicMock | None,
        entries: list[MagicMock],
        pedigrees: list[MagicMock],
        cache: SireStatsCache | None = None,
    ) -> PedigreeIndexCalculator:
        db = MagicMock()

        mock_race_q = MagicMock()
        mock_race_q.filter.return_value.first.return_value = race

        mock_entry_q = MagicMock()
        mock_entry_q.filter.return_value.all.return_value = entries

        mock_ped_q = MagicMock()
        mock_ped_q.filter.return_value.all.return_value = pedigrees

        db.query.side_effect = [mock_race_q, mock_entry_q, mock_ped_q]
        calc = PedigreeIndexCalculator(db=db)

        if cache:
            calc._cache = cache
        else:
            empty_cache = SireStatsCache(db)
            empty_cache._loaded = True
            calc._cache = empty_cache
        return calc

    def test_unknown_race_returns_empty(self) -> None:
        """存在しない race_id → 空dict"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = PedigreeIndexCalculator(db=db)
        empty_cache = SireStatsCache(db)
        empty_cache._loaded = True
        calc._cache = empty_cache
        assert calc.calculate_batch(race_id=9999) == {}

    def test_no_entries_returns_empty(self) -> None:
        """エントリなし → 空dict"""
        race = _make_mock_race(1)
        calc = self._build_batch_calc(race, entries=[], pedigrees=[])
        assert calc.calculate_batch(race_id=1) == {}

    def test_no_pedigree_returns_default(self) -> None:
        """血統未登録の馬 → SPEED_INDEX_MEAN"""
        race = _make_mock_race(1)
        entries = [_make_mock_entry(101)]
        calc = self._build_batch_calc(race, entries, pedigrees=[])
        result = calc.calculate_batch(race_id=1)
        assert result[101] == SPEED_INDEX_MEAN

    def test_all_horse_ids_returned(self) -> None:
        """全馬の horse_id がキーとして返る"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        entries = [_make_mock_entry(hid) for hid in [101, 102, 103]]
        pedigrees = [_make_mock_pedigree(hid) for hid in [101, 102, 103]]
        calc = self._build_batch_calc(race, entries, pedigrees)
        result = calc.calculate_batch(race_id=1)
        assert set(result.keys()) == {101, 102, 103}

    def test_strong_sire_higher_than_weak(self) -> None:
        """得意条件の父 > 苦手条件の父"""
        race = _make_mock_race(1, surface="芝", distance=2000, course="05")
        entries = [_make_mock_entry(101), _make_mock_entry(102)]
        pedigrees = [
            _make_mock_pedigree(101, sire="TestSire", sire_of_dam="TestSire"),
            _make_mock_pedigree(102, sire="WeakSire", sire_of_dam="WeakSire"),
        ]
        cache = _make_cache_with_data()
        calc = self._build_batch_calc(race, entries, pedigrees, cache=cache)
        result = calc.calculate_batch(race_id=1)
        assert result[101] > result[102]

    def test_mixed_pedigree_missing_returns_default(self) -> None:
        """血統あり馬とない馬が混在 → ない馬は SPEED_INDEX_MEAN"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        entries = [_make_mock_entry(101), _make_mock_entry(102)]
        pedigrees = [_make_mock_pedigree(101)]  # 102は血統なし
        calc = self._build_batch_calc(race, entries, pedigrees)
        result = calc.calculate_batch(race_id=1)
        assert result[102] == SPEED_INDEX_MEAN

    def test_all_scores_in_range(self) -> None:
        """全スコアが 0-100 範囲"""
        race = _make_mock_race(1, surface="芝", distance=2000)
        entries = [_make_mock_entry(hid) for hid in [101, 102]]
        pedigrees = [_make_mock_pedigree(hid) for hid in [101, 102]]
        cache = _make_cache_with_data()
        calc = self._build_batch_calc(race, entries, pedigrees, cache=cache)
        result = calc.calculate_batch(race_id=1)
        for score in result.values():
            assert 0.0 <= score <= 100.0
