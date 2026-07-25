"""DM シグナルタグ算出ロジックのテスト"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.indices.dm_signals import (
    SIGNAL_MULTI_SOURCE_MATCH,
    SIGNAL_POPULAR_DOWNSIDE,
    SIGNAL_SINGLE_SOURCE_MATCH,
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
    nb_ave_rank: int | None = None
    km_rank: int | None = None
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
    """DM完全揃い必須の軸/警戒シグナルはDM欠損レースで付かない（穴badgeはDM欠損でも計算対象）。"""
    horses = [
        Horse(1, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=70.0),
        Horse(2, composite_index=70.0, jvan_time_dm=65.0, jvan_battle_dm=65.0),
    ]
    compute_dm_signals(horses, popularity_map={1: 1, 2: 2})
    assert SIGNAL_TRIPLE_MATCH not in (horses[0].dm_signals or [])
    assert SIGNAL_TOP_PREMIUM not in (horses[0].dm_signals or [])
    assert SIGNAL_POPULAR_DOWNSIDE not in (horses[0].dm_signals or [])


def test_scratched_horse_excluded_from_population() -> None:
    """取消馬（DM欠損）を exclude_horse_numbers で除外すれば残りの馬にシグナルが付く"""
    horses = [
        Horse(1, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),  # 取消馬
        Horse(2, composite_index=75.0, jvan_time_dm=80.0, jvan_battle_dm=80.0),
        Horse(3, composite_index=60.0, jvan_time_dm=60.0, jvan_battle_dm=60.0),
    ]
    compute_dm_signals(
        horses,
        popularity_map={2: 1, 3: 2},
        exclude_horse_numbers={1},
    )
    # 取消馬はシグナルなし（空リスト）のまま
    assert horses[0].dm_signals == []
    # 残りの馬は取消馬を除いた母集団で判定され、シグナルが付く
    assert SIGNAL_TRIPLE_MATCH in (horses[1].dm_signals or [])


def test_scratched_horse_excluded_from_ranks() -> None:
    """除外馬は順位計算にも含まれない（除外馬が1位相当でも残り馬が rank=1 になる）"""
    horses = [
        Horse(1, composite_index=90.0, jvan_time_dm=90.0, jvan_battle_dm=90.0),  # 取消馬（最強）
        Horse(2, composite_index=75.0, jvan_time_dm=80.0, jvan_battle_dm=80.0),
        Horse(3, composite_index=60.0, jvan_time_dm=60.0, jvan_battle_dm=60.0),
    ]
    compute_dm_signals(
        horses,
        popularity_map={2: 1, 3: 2},
        exclude_horse_numbers={1},
    )
    # 除外馬を除くと馬番2が base/time/battle すべて1位 → 三冠一致
    assert SIGNAL_TRIPLE_MATCH in (horses[1].dm_signals or [])
    assert horses[0].dm_signals == []


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


# ---------------------------------------------------------------------------
# 穴badge (MULTI/SINGLE_SOURCE_MATCH) — 2026-07-25 再設計
# [[jra_upset_badge_redesign]]: 4情報源(穴ぐさ/netkeiba/kichiuma/DM-battle)の
# 一致数(badge_cnt)を単勝オッズ≥10の馬にのみ付与する。
# ---------------------------------------------------------------------------


def test_multi_source_match_two_sources() -> None:
    """単勝≥10 ∧ 穴ぐさABC + netkeiba上位3 の2ソース一致 → 複数指数一致穴"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=2),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0})
    assert SIGNAL_MULTI_SOURCE_MATCH in (horses[0].dm_signals or [])
    assert SIGNAL_SINGLE_SOURCE_MATCH not in (horses[0].dm_signals or [])


def test_single_source_match_one_source() -> None:
    """単勝≥10 ∧ 情報源1つのみ一致 → 指数一致穴（複数指数一致穴ではない）"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0})
    assert SIGNAL_SINGLE_SOURCE_MATCH in (horses[0].dm_signals or [])
    assert SIGNAL_MULTI_SOURCE_MATCH not in (horses[0].dm_signals or [])


def test_upset_badge_requires_min_odds() -> None:
    """単勝10倍未満は badge_cnt が高くても対象外（人気薄限定）"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=1, km_rank=1),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 8.0, 2: 2.0})
    assert (horses[0].dm_signals or []) == []


def test_upset_badge_no_signal_without_win_odds_map() -> None:
    """win_odds_map なしでは対象オッズ判定ができないため穴badgeは付かない"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=1),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses)
    assert (horses[0].dm_signals or []) == []


def test_upset_badge_counts_dm_battle_with_partial_coverage() -> None:
    """DM battle が一部の馬にしかなくても(全頭DM必須の三冠一致等と異なり)badge_cntに使える"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=90.0),  # battle最上位
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=50.0),
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),  # DM欠損でも可
    ]
    compute_dm_signals(horses, win_odds_map={1: 12.0, 2: 2.0, 3: 20.0})
    assert SIGNAL_SINGLE_SOURCE_MATCH in (horses[0].dm_signals or [])


def test_upset_badge_only_one_horse_per_race() -> None:
    """複数頭がbadge_cnt>=1でも、レースにつきbadge_cnt最大の1頭のみタグが付く
    (2026-07-25追加改修: 合算100%基準を断念しK=1に絞り込み)"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=2),  # badge_cnt=2
        Horse(2, composite_index=45.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),  # badge_cnt=1
        Horse(3, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 12.0, 3: 2.0})
    assert SIGNAL_MULTI_SOURCE_MATCH in (horses[0].dm_signals or [])
    assert (horses[1].dm_signals or []) == []
    assert (horses[2].dm_signals or []) == []


def test_upset_badge_tie_break_by_composite_index() -> None:
    """badge_cntが同点の場合はcomposite_indexが高い方が選ばれる"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # badge_cnt=1, composite低い
        Horse(2, composite_index=50.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),  # badge_cnt=1, composite高い
        Horse(3, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 12.0, 3: 2.0})
    assert (horses[0].dm_signals or []) == []
    assert SIGNAL_SINGLE_SOURCE_MATCH in (horses[1].dm_signals or [])


def test_upset_badge_not_popularity_dependent() -> None:
    """穴badgeは popularity_map 非依存（win_odds_map のみで判定）"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=2),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0}, popularity_map=None)
    assert SIGNAL_MULTI_SOURCE_MATCH in (horses[0].dm_signals or [])
