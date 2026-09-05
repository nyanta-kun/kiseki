"""穴シグナル(SIGNAL_UPSET_CANDIDATE/SIGNAL_ANAGUSA_ELITE)算出ロジックのテスト

2026-07-25 全面簡素化([[jra_upset_badge_redesign]]): 軸/警戒タグは
recommend_rankへの一本化に伴い廃止。穴タグも単一の「穴」マークに統合。
2026-07-26 「特穴」追加([[jra_anagusa_elite_signal]]): 穴ぐさ×指数上位3×
単勝10倍以上のROI狙いタグ。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.indices.dm_signals import (
    SIGNAL_ANAGUSA_ELITE,
    SIGNAL_HEIHACHI,
    SIGNAL_UPSET_CANDIDATE,
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
    place_probability: float | None = None
    dm_signals: list[str] | None = field(default=None)


def test_ranks_descending_basic() -> None:
    assert _ranks_descending([50.0, 80.0, 30.0]) == [2, 1, 3]


def test_ranks_descending_with_ties() -> None:
    assert _ranks_descending([50.0, 80.0, 80.0, 30.0]) == [3, 1, 1, 4]


def test_ranks_descending_with_none() -> None:
    assert _ranks_descending([50.0, None, 30.0, 80.0]) == [2, None, 3, 1]


def _fillers(numbers: list[int]) -> list[Horse]:
    """comp順位を押し下げるための高composite馬(特穴条件との誤発火回避用)。"""
    return [
        Horse(n, composite_index=100.0 - i, jvan_time_dm=None, jvan_battle_dm=None)
        for i, n in enumerate(numbers)
    ]


def test_popularity_from_odds() -> None:
    odds_map = {1: 5.5, 2: 2.1, 3: 8.0, 4: None, 5: 8.0}
    pops = popularity_from_odds([1, 2, 3, 4, 5], odds_map)
    assert pops[2] == 1
    assert pops[1] == 2
    assert pops[3] == pops[5]
    assert 4 not in pops


# ---------------------------------------------------------------------------
# 穴badge (SIGNAL_UPSET_CANDIDATE) — 2026-07-25 再設計・単一タグに統合
# 4情報源(穴ぐさ/netkeiba/kichiuma/DM-battle)の一致数(badge_cnt)が最大の1頭のみ
# (同点はcomposite_index降順)に、単勝オッズ≥10の馬にのみ付与する。
# ---------------------------------------------------------------------------


def test_upset_candidate_with_two_sources() -> None:
    """単勝≥10 ∧ 穴ぐさABC + netkeiba上位3 の2ソース一致 → 穴

    (comp順位を4位以下にするフィラー馬を追加し、特穴条件の誤発火を回避)
    """
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=2),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        *_fillers([3, 4, 5]),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0})
    assert SIGNAL_UPSET_CANDIDATE in (horses[0].dm_signals or [])
    assert (horses[1].dm_signals or []) == []


def test_upset_candidate_with_one_source() -> None:
    """単勝≥10 ∧ 情報源1つのみ一致でも 穴 が付く"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        *_fillers([3, 4, 5]),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0})
    assert SIGNAL_UPSET_CANDIDATE in (horses[0].dm_signals or [])


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
    """DM battle が一部の馬にしかなくてもbadge_cntに使える(DM完全揃い不要)"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=90.0),  # battle最上位
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=50.0),
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),  # DM欠損でも可
    ]
    compute_dm_signals(horses, win_odds_map={1: 12.0, 2: 2.0, 3: 20.0})
    assert SIGNAL_UPSET_CANDIDATE in (horses[0].dm_signals or [])


def test_upset_badge_only_one_horse_per_race() -> None:
    """複数頭がbadge_cnt>=1でも、レースにつきbadge_cnt最大の1頭のみタグが付く

    フィラー馬3頭で anagusa 馬の composite順位を4位以下に押し下げ、
    特穴(composite上位3必須)が誤発火しないようにする。
    """
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=2),  # badge_cnt=2
        Horse(2, composite_index=45.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),  # badge_cnt=1
        Horse(3, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(4, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(5, composite_index=65.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(6, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 12.0, 3: 2.0, 4: 3.0, 5: 4.0, 6: 5.0})
    assert SIGNAL_UPSET_CANDIDATE in (horses[0].dm_signals or [])
    assert (horses[1].dm_signals or []) == []
    assert (horses[2].dm_signals or []) == []


def test_upset_badge_tie_break_by_composite_index() -> None:
    """badge_cntが同点の場合はcomposite_indexが高い方が選ばれる

    フィラー馬3頭で anagusa 馬の composite順位を4位以下に押し下げる。
    """
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # badge_cnt=1, composite低い
        Horse(2, composite_index=50.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),  # badge_cnt=1, composite高い
        Horse(3, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(4, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(5, composite_index=65.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(6, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 12.0, 3: 2.0, 4: 3.0, 5: 4.0, 6: 5.0})
    assert (horses[0].dm_signals or []) == []
    assert SIGNAL_UPSET_CANDIDATE in (horses[1].dm_signals or [])


def test_upset_badge_not_popularity_dependent() -> None:
    """穴badgeは popularity_map 非依存（win_odds_map のみで判定）"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=2),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        *_fillers([3, 4, 5]),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0}, popularity_map=None)
    assert SIGNAL_UPSET_CANDIDATE in (horses[0].dm_signals or [])


def test_no_upset_candidate_when_no_horse_qualifies() -> None:
    """該当馬がいなければ誰にも付かない"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0})
    assert (horses[0].dm_signals or []) == []
    assert (horses[1].dm_signals or []) == []


def test_scratched_horse_excluded_from_population() -> None:
    """取消馬は判定の母集団から除外され、シグナル判定に影響しない"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # 取消馬
        Horse(2, composite_index=50.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),
        Horse(3, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        *_fillers([4, 5, 6]),
    ]
    compute_dm_signals(
        horses,
        win_odds_map={1: 15.0, 2: 12.0, 3: 2.0},
        exclude_horse_numbers={1},
    )
    # 取消馬はシグナルなし（空リスト）のまま
    assert horses[0].dm_signals == []
    # 残りの母集団の中で穴候補が選ばれる
    assert SIGNAL_UPSET_CANDIDATE in (horses[1].dm_signals or [])


def test_empty_horses_list() -> None:
    """空リストでもエラーにならない"""
    compute_dm_signals([])


# ---------------------------------------------------------------------------
# 特穴 (SIGNAL_ANAGUSA_ELITE) — 2026-07-26 追加
# 穴ぐさ(A/B/C) ∧ composite順位≤3 ∧ 単勝オッズ≥10。「穴」とは独立の条件。
# ---------------------------------------------------------------------------


def test_anagusa_elite_basic() -> None:
    """穴ぐさ ∧ composite上位3 ∧ オッズ10倍以上 → 特穴"""
    horses = [
        Horse(1, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # comp2位
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),  # comp1位
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),  # comp3位
        Horse(4, composite_index=50.0, jvan_time_dm=None, jvan_battle_dm=None),  # comp4位
    ]
    compute_dm_signals(horses, win_odds_map={1: 12.0, 2: 2.0, 3: 20.0, 4: 30.0})
    assert SIGNAL_ANAGUSA_ELITE in (horses[0].dm_signals or [])


def test_anagusa_elite_requires_top3_composite() -> None:
    """composite順位が4位以下では特穴が付かない"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # comp4位(下位)
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(3, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(4, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 12.0, 2: 2.0, 3: 3.0, 4: 4.0})
    assert SIGNAL_ANAGUSA_ELITE not in (horses[0].dm_signals or [])


def test_anagusa_elite_requires_min_odds() -> None:
    """単勝オッズ10倍未満では特穴が付かない"""
    horses = [
        Horse(1, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 9.9, 2: 2.0, 3: 3.0})
    assert SIGNAL_ANAGUSA_ELITE not in (horses[0].dm_signals or [])


def test_anagusa_elite_requires_anagusa_pick() -> None:
    """穴ぐさピックがなければcomposite上位3・高オッズでも特穴は付かない"""
    horses = [
        Horse(1, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 12.0, 2: 2.0, 3: 3.0})
    assert (horses[0].dm_signals or []) == []


def test_anagusa_elite_suppresses_upset_candidate_on_same_horse() -> None:
    """特穴は穴の上位互換のため、同一馬が両方の条件を満たしても特穴のみ表示する
    (2026-07-26追加: ユーザー指示により穴を非表示化)"""
    horses = [
        Horse(1, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A", nb_ave_rank=1, km_rank=1),  # badge_cnt=3・comp1位
        Horse(2, composite_index=50.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 2.0})
    signals = horses[0].dm_signals or []
    assert SIGNAL_ANAGUSA_ELITE in signals
    assert SIGNAL_UPSET_CANDIDATE not in signals


def test_upset_candidate_kept_when_different_horse_gets_elite() -> None:
    """穴の該当馬と特穴の該当馬が別なら、穴はそのまま表示される"""
    horses = [
        Horse(1, composite_index=40.0, jvan_time_dm=None, jvan_battle_dm=None,
              nb_ave_rank=2, km_rank=2),  # badge_cnt=2(穴ぐさ無し)・comp下位 → 穴で勝つ
        Horse(2, composite_index=90.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # badge_cnt=1・comp1位 → 特穴のみ
        Horse(3, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None),
        Horse(4, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None),
    ]
    compute_dm_signals(horses, win_odds_map={1: 15.0, 2: 12.0, 3: 3.0, 4: 4.0})
    assert SIGNAL_UPSET_CANDIDATE in (horses[0].dm_signals or [])
    assert SIGNAL_ANAGUSA_ELITE in (horses[1].dm_signals or [])
    assert SIGNAL_UPSET_CANDIDATE not in (horses[1].dm_signals or [])


def test_anagusa_elite_multiple_horses_allowed() -> None:
    """特穴は1レース複数頭に付与されうる(badge_cnt系と異なりK=1キャップなし)"""
    horses = [
        Horse(1, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="A"),  # comp2位
        Horse(2, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None,
              anagusa_rank="B"),  # comp1位
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None),  # comp3位
    ]
    compute_dm_signals(horses, win_odds_map={1: 12.0, 2: 11.0, 3: 20.0})
    assert SIGNAL_ANAGUSA_ELITE in (horses[0].dm_signals or [])
    assert SIGNAL_ANAGUSA_ELITE in (horses[1].dm_signals or [])


# ---------------------------------------------------------------------------
# 平八badge (SIGNAL_HEIHACHI) — 2026-09-06追加 [[jra_heihachi_badge]]
# 平地OP特別以上 ∧ composite順位≤3 ∧ 単勝10〜40倍 ∧ 複勝確率≥0.30
# ---------------------------------------------------------------------------


def _heihachi_horses(odds_target: float, place_prob: float | None) -> list[Horse]:
    """1番が comp1位・対象、2〜4番は comp下位のフィラー(人気側)。"""
    return [
        Horse(1, composite_index=80.0, jvan_time_dm=None, jvan_battle_dm=None,
              place_probability=place_prob),
        Horse(2, composite_index=70.0, jvan_time_dm=None, jvan_battle_dm=None,
              place_probability=0.60),
        Horse(3, composite_index=60.0, jvan_time_dm=None, jvan_battle_dm=None,
              place_probability=0.50),
        Horse(4, composite_index=50.0, jvan_time_dm=None, jvan_battle_dm=None,
              place_probability=0.40),
    ], {1: odds_target, 2: 2.0, 3: 3.0, 4: 4.0}


def test_heihachi_basic() -> None:
    """OP特別 ∧ 指数1位 ∧ 20倍 ∧ 複勝確率0.45 → 平八"""
    horses, odds = _heihachi_horses(20.0, 0.45)
    compute_dm_signals(horses, win_odds_map=odds, grade="OP特別")
    assert SIGNAL_HEIHACHI in (horses[0].dm_signals or [])


def test_heihachi_requires_selected_grade() -> None:
    """平場(grade=None)では付与しない — レース選定がROIを担っているため"""
    horses, odds = _heihachi_horses(20.0, 0.45)
    compute_dm_signals(horses, win_odds_map=odds, grade=None)
    assert SIGNAL_HEIHACHI not in (horses[0].dm_signals or [])


def test_heihachi_excludes_jump_races() -> None:
    """障害(J.G3)は母数が少なく別物なので対象外"""
    horses, odds = _heihachi_horses(20.0, 0.45)
    compute_dm_signals(horses, win_odds_map=odds, grade="J.G3")
    assert SIGNAL_HEIHACHI not in (horses[0].dm_signals or [])


def test_heihachi_odds_band_is_exclusive_at_both_ends() -> None:
    """単勝15倍未満・40倍以上は対象外（15.0は含み、40.0は含まない）"""
    for odds_v, expected in [(14.9, False), (15.0, True), (39.9, True), (40.0, False)]:
        horses, odds = _heihachi_horses(odds_v, 0.45)
        compute_dm_signals(horses, win_odds_map=odds, grade="G3")
        assert (SIGNAL_HEIHACHI in (horses[0].dm_signals or [])) is expected, odds_v


def test_heihachi_requires_place_probability() -> None:
    """複勝確率が閾値未満 / 欠損なら付与しない"""
    for pp, expected in [(0.39, False), (0.40, True), (None, False)]:
        horses, odds = _heihachi_horses(20.0, pp)
        compute_dm_signals(horses, win_odds_map=odds, grade="G1")
        assert (SIGNAL_HEIHACHI in (horses[0].dm_signals or [])) is expected, pp


def test_heihachi_requires_composite_top3() -> None:
    """composite順位4位以下は対象外"""
    horses = _fillers([2, 3, 4]) + [
        Horse(1, composite_index=10.0, jvan_time_dm=None, jvan_battle_dm=None,
              place_probability=0.50),
    ]
    compute_dm_signals(
        horses, win_odds_map={1: 15.0, 2: 2.0, 3: 3.0, 4: 4.0}, grade="OP特別"
    )
    assert SIGNAL_HEIHACHI not in (horses[-1].dm_signals or [])


def test_heihachi_ignores_scratched_horse() -> None:
    """取消馬には付与しない"""
    horses, odds = _heihachi_horses(20.0, 0.45)
    compute_dm_signals(
        horses, win_odds_map=odds, grade="OP特別", exclude_horse_numbers={1}
    )
    assert horses[0].dm_signals == []
