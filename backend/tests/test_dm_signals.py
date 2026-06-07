"""DM シグナルタグ算出ロジックのテスト"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.indices.dm_signals import (
    SIGNAL_ANAGUSA_DM,
    SIGNAL_ANAGUSA_DM_TIME,
    SIGNAL_DM_BIG_DARK,
    SIGNAL_DM_HIGH_ODDS,
    SIGNAL_POPULAR_DOWNSIDE,
    SIGNAL_TOP_PREMIUM,
    SIGNAL_TRIPLE_MATCH,
    _ranks_descending,
    compute_dm_signals,
    popularity_from_odds,
)


@dataclass
class Horse:
    """テスト用の最小 Horse オブジェクト"""

    horse_number: int
    composite_index: float
    jvan_time_dm: float | None
    jvan_battle_dm: float | None
    anagusa_rank: str | None = None
    dm_signals: list[str] | None = field(default=None)


def test_ranks_descending_basic() -> None:
    assert _ranks_descending([50.0, 80.0, 30.0]) == [2, 1, 3]


def test_ranks_descending_with_ties() -> None:
    assert _ranks_descending([50.0, 80.0, 80.0, 30.0]) == [3, 1, 1, 4]


def test_ranks_descending_with_none() -> None:
    assert _ranks_descending([50.0, None, 30.0, 80.0]) == [2, None, 3, 1]


def test_triple_match() -> None:
    horses = [
        Horse(1, composite_index=55.0, jvan_time_dm=75.0, jvan_battle_dm=80.0),
        Horse(2, composite_index=50.0, jvan_time_dm=65.0, jvan_battle_dm=70.0),
        Horse(3, composite_index=45.0, jvan_time_dm=60.0, jvan_battle_dm=65.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2, 3: 3})
    assert SIGNAL_TRIPLE_MATCH in (horses[0].dm_signals or [])
    assert SIGNAL_TRIPLE_MATCH not in (horses[1].dm_signals or [])


def test_top_premium() -> None:
    """composite≥60 ∧ battle≥65 ∧ composite順位≤2 → 高得点鉄板"""
    horses = [
        Horse(1, composite_index=62.0, jvan_time_dm=70.0, jvan_battle_dm=68.0),  # comp2位
        Horse(2, composite_index=55.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),  # base<60
        Horse(3, composite_index=70.0, jvan_time_dm=60.0, jvan_battle_dm=60.0),  # battle<65
    ]
    compute_dm_signals(horses)
    assert SIGNAL_TOP_PREMIUM in (horses[0].dm_signals or [])
    assert SIGNAL_TOP_PREMIUM not in (horses[1].dm_signals or [])
    assert SIGNAL_TOP_PREMIUM not in (horses[2].dm_signals or [])


def test_top_premium_rank_capped_to_two() -> None:
    """絶対しきい値を満たす馬が3頭以上いても composite 上位2頭のみに限定される。

    鉄板印の乱発防止 (2026-06-07)。
    """
    horses = [
        Horse(1, composite_index=75.0, jvan_time_dm=70.0, jvan_battle_dm=80.0),  # comp1位 ◎
        Horse(2, composite_index=70.0, jvan_time_dm=70.0, jvan_battle_dm=78.0),  # comp2位 ◎
        Horse(3, composite_index=65.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),  # comp3位 → 閾値満たすが除外
        Horse(4, composite_index=62.0, jvan_time_dm=70.0, jvan_battle_dm=66.0),  # comp4位 → 同上
    ]
    compute_dm_signals(horses)
    got = [h.horse_number for h in horses if SIGNAL_TOP_PREMIUM in (h.dm_signals or [])]
    assert got == [1, 2], f"上位2頭のみのはず: {got}"


def test_anagusa_dm() -> None:
    """anagusa∈{A,B} ∧ battle=1 ∧ 人気≥5 → 穴ぐさDM"""
    horses = [
        Horse(1, composite_index=50.0, jvan_time_dm=70.0, jvan_battle_dm=80.0, anagusa_rank="A"),
        Horse(2, composite_index=60.0, jvan_time_dm=72.0, jvan_battle_dm=75.0, anagusa_rank=None),
        Horse(3, composite_index=55.0, jvan_time_dm=68.0, jvan_battle_dm=70.0, anagusa_rank="A"),
    ]
    compute_dm_signals(horses, popularity_map={1: 6, 2: 1, 3: 4})
    assert SIGNAL_ANAGUSA_DM in (horses[0].dm_signals or [])  # A + battle=1 + pop=6
    assert SIGNAL_ANAGUSA_DM not in (horses[2].dm_signals or [])  # A + battle≠1


def test_anagusa_dm_skipped_when_popular() -> None:
    horses = [
        Horse(1, composite_index=50.0, jvan_time_dm=70.0, jvan_battle_dm=80.0, anagusa_rank="A"),
        Horse(2, composite_index=60.0, jvan_time_dm=72.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 2, 2: 1})  # 人気2は穴ではない
    assert SIGNAL_ANAGUSA_DM not in (horses[0].dm_signals or [])


def test_dm_big_dark() -> None:
    """battle=1 ∧ 人気≥7 ∧ battle値≥65 → DM大穴"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=60.0, jvan_battle_dm=70.0),  # battle値65以上
        Horse(2, composite_index=80.0, jvan_time_dm=70.0, jvan_battle_dm=68.0),
        Horse(3, composite_index=60.0, jvan_time_dm=65.0, jvan_battle_dm=66.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 8, 2: 1, 3: 2})
    assert SIGNAL_DM_BIG_DARK in (horses[0].dm_signals or [])


def test_dm_big_dark_battle_value_required() -> None:
    """battle値が65未満なら大穴にならない"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=50.0, jvan_battle_dm=55.0),  # battle値<65
        Horse(2, composite_index=80.0, jvan_time_dm=70.0, jvan_battle_dm=50.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 8, 2: 1})
    assert SIGNAL_DM_BIG_DARK not in (horses[0].dm_signals or [])


def test_dm_high_odds() -> None:
    """battle=1 ∧ オッズ≥10 ∧ time≤2 → DM高オッズ"""
    horses = [
        Horse(1, composite_index=50.0, jvan_time_dm=70.0, jvan_battle_dm=75.0),
        Horse(2, composite_index=60.0, jvan_time_dm=75.0, jvan_battle_dm=70.0),
        Horse(3, composite_index=70.0, jvan_time_dm=68.0, jvan_battle_dm=68.0),
    ]
    # 1番馬: time=2位 (75 vs 70)、battle=1位、オッズ12.0 → タグつく
    compute_dm_signals(
        horses,
        popularity_map={1: 6, 2: 1, 3: 2},
        win_odds_map={1: 12.0, 2: 2.0, 3: 4.0},
    )
    assert SIGNAL_DM_HIGH_ODDS in (horses[0].dm_signals or [])


def test_dm_high_odds_requires_time_le_2() -> None:
    horses = [
        Horse(1, composite_index=50.0, jvan_time_dm=60.0, jvan_battle_dm=80.0),  # time=3位
        Horse(2, composite_index=60.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),
        Horse(3, composite_index=70.0, jvan_time_dm=72.0, jvan_battle_dm=68.0),
    ]
    compute_dm_signals(
        horses,
        popularity_map={1: 6, 2: 1, 3: 2},
        win_odds_map={1: 12.0, 2: 2.0, 3: 4.0},
    )
    assert SIGNAL_DM_HIGH_ODDS not in (horses[0].dm_signals or [])


def test_anagusa_dm_time() -> None:
    """anagusa=A ∧ time=1 → 穴ぐさ+DMtime"""
    horses = [
        Horse(1, composite_index=50.0, jvan_time_dm=80.0, jvan_battle_dm=70.0, anagusa_rank="A"),
        Horse(2, composite_index=60.0, jvan_time_dm=70.0, jvan_battle_dm=80.0, anagusa_rank="B"),
    ]
    compute_dm_signals(horses, popularity_map={1: 5, 2: 1})
    assert SIGNAL_ANAGUSA_DM_TIME in (horses[0].dm_signals or [])
    assert SIGNAL_ANAGUSA_DM_TIME not in (horses[1].dm_signals or [])  # B(not A) + time≠1


def test_popular_downside() -> None:
    """人気≤3 ∧ base≥4位 ∧ battle≥4位 → 人気下振れ"""
    horses = [
        Horse(1, composite_index=80.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),  # base=1
        Horse(2, composite_index=75.0, jvan_time_dm=68.0, jvan_battle_dm=68.0),  # base=2
        Horse(3, composite_index=70.0, jvan_time_dm=66.0, jvan_battle_dm=66.0),  # base=3
        Horse(4, composite_index=65.0, jvan_time_dm=64.0, jvan_battle_dm=64.0),  # base=4
        Horse(5, composite_index=50.0, jvan_time_dm=50.0, jvan_battle_dm=50.0),  # base=5, battle=5
    ]
    # 5番馬は最人気だが、base=5 / battle=5 → 人気下振れ
    compute_dm_signals(horses, popularity_map={5: 1, 1: 2, 2: 3, 3: 4, 4: 5})
    assert SIGNAL_POPULAR_DOWNSIDE in (horses[4].dm_signals or [])
    assert SIGNAL_POPULAR_DOWNSIDE not in (horses[0].dm_signals or [])


def test_no_signals_when_dm_missing() -> None:
    horses = [
        Horse(1, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=70.0),
        Horse(2, composite_index=70.0, jvan_time_dm=65.0, jvan_battle_dm=65.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2})
    assert horses[0].dm_signals == []
    assert horses[1].dm_signals == []


def test_no_popularity_skips_popularity_signals() -> None:
    """popularity_map なしなら 人気依存タグ (ANAGUSA_DM/DM_BIG_DARK/POPULAR_DOWNSIDE) は発動しない"""
    horses = [
        Horse(1, composite_index=80.0, jvan_time_dm=80.0, jvan_battle_dm=80.0, anagusa_rank="A"),
        Horse(2, composite_index=70.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map=None)
    # 三冠一致と高得点鉄板は人気非依存なので発動する
    assert SIGNAL_TRIPLE_MATCH in (horses[0].dm_signals or [])
    assert SIGNAL_TOP_PREMIUM in (horses[0].dm_signals or [])
    # 人気依存のものは発動しない
    assert SIGNAL_ANAGUSA_DM not in (horses[0].dm_signals or [])
    assert SIGNAL_DM_BIG_DARK not in (horses[0].dm_signals or [])


def test_popularity_from_odds() -> None:
    odds_map = {1: 5.5, 2: 2.1, 3: 8.0, 4: None, 5: 8.0}
    pops = popularity_from_odds([1, 2, 3, 4, 5], odds_map)
    assert pops[2] == 1
    assert pops[1] == 2
    assert pops[3] == pops[5]
    assert 4 not in pops


def test_strongest_signal_combination() -> None:
    """三冠一致 ∧ 高得点鉄板 が同時に成立"""
    horses = [
        Horse(1, composite_index=70.0, jvan_time_dm=80.0, jvan_battle_dm=80.0),
        Horse(2, composite_index=65.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2})
    assert SIGNAL_TRIPLE_MATCH in (horses[0].dm_signals or [])
    assert SIGNAL_TOP_PREMIUM in (horses[0].dm_signals or [])


def test_triple_match_denied_by_course() -> None:
    """三冠一致は福島/阪神/京都では低 ROI のため発動しない"""
    horses = [
        Horse(1, composite_index=55.0, jvan_time_dm=75.0, jvan_battle_dm=80.0),
        Horse(2, composite_index=50.0, jvan_time_dm=65.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2}, course_name="福島")
    assert SIGNAL_TRIPLE_MATCH not in (horses[0].dm_signals or [])


def test_triple_match_denied_by_segment() -> None:
    """三冠一致は芝×マイルでは低 ROI のため発動しない"""
    horses = [
        Horse(1, composite_index=55.0, jvan_time_dm=75.0, jvan_battle_dm=80.0),
        Horse(2, composite_index=50.0, jvan_time_dm=65.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2},
                       surface="芝", distance=1600)
    assert SIGNAL_TRIPLE_MATCH not in (horses[0].dm_signals or [])


def test_triple_match_allowed_in_safe_segment() -> None:
    """三冠一致は芝×スプリントなら発動 (ROI 95%)"""
    horses = [
        Horse(1, composite_index=55.0, jvan_time_dm=75.0, jvan_battle_dm=80.0),
        Horse(2, composite_index=50.0, jvan_time_dm=65.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2},
                       course_name="新潟", surface="芝", distance=1200)
    assert SIGNAL_TRIPLE_MATCH in (horses[0].dm_signals or [])


def test_anagusa_dm_denied_in_tokyo() -> None:
    """穴ぐさDM は東京 (ROI 21%) では発動しない"""
    horses = [
        Horse(1, composite_index=50.0, jvan_time_dm=70.0, jvan_battle_dm=80.0, anagusa_rank="A"),
        Horse(2, composite_index=60.0, jvan_time_dm=72.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 6, 2: 1}, course_name="東京")
    assert SIGNAL_ANAGUSA_DM not in (horses[0].dm_signals or [])


def test_popular_downside_denied_in_fukushima() -> None:
    """人気下振れ警戒は福島 (ROI 95%) では発動しない (実は来やすい)"""
    horses = [
        Horse(1, composite_index=80.0, jvan_time_dm=70.0, jvan_battle_dm=70.0),
        Horse(2, composite_index=75.0, jvan_time_dm=68.0, jvan_battle_dm=68.0),
        Horse(3, composite_index=70.0, jvan_time_dm=66.0, jvan_battle_dm=66.0),
        Horse(4, composite_index=65.0, jvan_time_dm=64.0, jvan_battle_dm=64.0),
        Horse(5, composite_index=50.0, jvan_time_dm=50.0, jvan_battle_dm=50.0),
    ]
    compute_dm_signals(horses,
                       popularity_map={5: 1, 1: 2, 2: 3, 3: 4, 4: 5},
                       course_name="福島")
    assert SIGNAL_POPULAR_DOWNSIDE not in (horses[4].dm_signals or [])


def test_no_filter_when_no_race_info() -> None:
    """course/surface/distance 省略時は旧挙動互換 (フィルタなし)"""
    horses = [
        Horse(1, composite_index=55.0, jvan_time_dm=75.0, jvan_battle_dm=80.0),
        Horse(2, composite_index=50.0, jvan_time_dm=65.0, jvan_battle_dm=70.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2})
    assert SIGNAL_TRIPLE_MATCH in (horses[0].dm_signals or [])
