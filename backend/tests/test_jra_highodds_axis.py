"""JRA 高オッズ穴 複勝＋ワイド軸 推奨ロジックのユニットテスト。

検証仕様 (memory: highodds_place_wide_recommendation):
  軸 = 単勝≥10 × composite上位4 × place_prob上位2 × バッジ。
  ワイド相手 = モデルcomposite1位。
"""
from __future__ import annotations

from src.indices.buy_signal import (
    HIGHODDS_MAX_COMP_RANK,
    HIGHODDS_MAX_PP_RANK,
    HIGHODDS_MIN_ODDS,
    jra_build_highodds_pick,
    jra_highodds_has_badge,
    jra_is_place_axis,
)


def test_thresholds() -> None:
    assert HIGHODDS_MIN_ODDS == 10.0
    assert HIGHODDS_MAX_COMP_RANK == 4
    assert HIGHODDS_MAX_PP_RANK == 2


def test_badge_any_sources() -> None:
    assert jra_highodds_has_badge("A", None, None, None) is True
    assert jra_highodds_has_badge("C", None, None, None) is True
    assert jra_highodds_has_badge(None, 3, None, None) is True   # netkeiba 順位≤3
    assert jra_highodds_has_badge(None, 4, None, None) is False  # netkeiba 順位>3
    assert jra_highodds_has_badge(None, None, 1, None) is True   # kichiuma 順位≤3
    assert jra_highodds_has_badge(None, None, None, ["DM大穴"]) is True
    assert jra_highodds_has_badge(None, None, None, []) is False
    assert jra_highodds_has_badge(None, None, None, None) is False


def test_axis_all_conditions() -> None:
    # 全条件成立(オッズ12・comp2位・pp1位・穴B)
    assert jra_is_place_axis(12.0, 2, 1, "B", None, None, None) is True


def test_axis_rejects_low_odds() -> None:
    assert jra_is_place_axis(9.9, 1, 1, "A", None, None, None) is False


def test_axis_rejects_low_comp_rank() -> None:
    # composite 5位はモデル絞り外
    assert jra_is_place_axis(15.0, 5, 1, "A", None, None, None) is False


def test_axis_rejects_low_pp_rank() -> None:
    # place_prob 3位は k≤2 絞り外
    assert jra_is_place_axis(15.0, 2, 3, "A", None, None, None) is False


def test_axis_rejects_no_badge() -> None:
    assert jra_is_place_axis(15.0, 2, 1, None, None, None, None) is False


def test_build_pick_with_partner() -> None:
    axis = {"horse_number": 7, "win_odds": 14.2, "anagusa_rank": "A", "dm_signals": ["DM大穴"]}
    pick = jra_build_highodds_pick(axis, comp_rank1_horse_number=3)
    assert pick["axis_horse_number"] == 7
    assert pick["place_bet"] is True
    assert pick["wide_partner_horse_number"] == 3
    assert "ワイド軸" in pick["rationale"]


def test_build_pick_axis_is_comp1() -> None:
    # 軸自身が composite 1位なら ワイド相手なし(複勝のみ)
    axis = {"horse_number": 5, "win_odds": 11.0, "anagusa_rank": "B"}
    pick = jra_build_highodds_pick(axis, comp_rank1_horse_number=5)
    assert pick["wide_partner_horse_number"] is None
    assert pick["rationale"].endswith("→ 複勝")
