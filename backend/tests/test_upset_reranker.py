"""人気薄(単勝10-15倍)複勝圏リランカーのユニットテスト。

検証仕様 (memory: upset_place_extraction):
  軸 = 単勝[10,15) × 非オッズリランカー上位1/3 × バッジ。
  バッジ2+ で "strong"、1+ で "standard"。
"""
from __future__ import annotations

import math

from src.indices.buy_signal import (
    UPSET_AXIS_BAND_MAX,
    UPSET_AXIS_BAND_MIN,
    jra_upset_axis_tier,
)
from src.indices.upset_reranker import (
    SUB_INDEX_COLUMNS,
    UpsetReranker,
    _rank_desc,
)


def test_band_constants() -> None:
    assert UPSET_AXIS_BAND_MIN == 10.0
    assert UPSET_AXIS_BAND_MAX == 15.0


def test_tier_standard_and_strong() -> None:
    assert jra_upset_axis_tier(12.0, 0.5, 0.3, 1) == "standard"
    assert jra_upset_axis_tier(12.0, 0.5, 0.3, 2) == "strong"
    assert jra_upset_axis_tier(12.0, 0.5, 0.3, 3) == "strong"


def test_tier_rejects_out_of_band() -> None:
    assert jra_upset_axis_tier(9.9, 0.9, 0.3, 2) is None
    assert jra_upset_axis_tier(15.0, 0.9, 0.3, 2) is None  # 15.0 は帯外（[10,15)）
    assert jra_upset_axis_tier(None, 0.9, 0.3, 2) is None


def test_tier_rejects_below_threshold_or_no_badge() -> None:
    assert jra_upset_axis_tier(12.0, 0.29, 0.3, 2) is None
    assert jra_upset_axis_tier(12.0, None, 0.3, 2) is None
    assert jra_upset_axis_tier(12.0, 0.5, None, 2) is None
    assert jra_upset_axis_tier(12.0, 0.5, 0.3, 0) is None
    assert jra_upset_axis_tier(12.0, 0.5, 0.3, None) is None


def test_rank_desc_ties_and_none() -> None:
    ranks = _rank_desc({1: 50.0, 2: 60.0, 3: 60.0, 4: None, 5: 40.0})
    assert ranks[2] == 1 and ranks[3] == 1  # 同値は min 方式
    assert ranks[1] == 3
    assert ranks[5] == 4
    assert ranks[4] is None


def _toy_artifact() -> dict:
    """pp のみ係数 1.0 の単純な logistic アーティファクト."""
    feats = ["pp", "comp_rank"]
    return {
        "features": feats,
        "median": {"pp": 0.1, "comp_rank": 5.0},
        "mean": [0.1, 5.0],
        "scale": [0.1, 3.0],
        "coef": [1.0, 0.0],
        "intercept": 0.0,
        "threshold": 0.5,
        "trained_at": "test",
    }


def test_score_race_sigmoid_and_universe() -> None:
    rr = UpsetReranker(_toy_artifact())
    horses = [
        # 本命(オッズ<10 → ユニバース外)
        {"horse_number": 1, "win_odds": 2.0, "place_probability": 0.6,
         "composite_index": 80.0},
        # 人気薄: pp=0.2 → z=(0.2-0.1)/0.1=1 → sigmoid(1)
        {"horse_number": 2, "win_odds": 12.0, "place_probability": 0.2,
         "composite_index": 60.0, "anagusa_rank": "A", "km_rank": 2},
        # オッズ未取得 → 含まれない
        {"horse_number": 3, "win_odds": None, "place_probability": 0.1},
    ]
    for h in horses:
        for c in SUB_INDEX_COLUMNS:
            h.setdefault(c, None)
    out = rr.score_race(horses, head_count=10)
    assert set(out.keys()) == {2}
    assert math.isclose(out[2]["ns"], 1.0 / (1.0 + math.exp(-1.0)), rel_tol=1e-9)
    # バッジ: 穴ぐさA + kichiuma2位 = 2
    assert out[2]["badge_cnt"] == 2


def test_score_race_badge_count_dm() -> None:
    rr = UpsetReranker(_toy_artifact())
    horses = [
        {"horse_number": 1, "win_odds": 11.0, "place_probability": 0.2,
         "composite_index": 60.0, "jvan_battle_dm": 70.0},
        {"horse_number": 2, "win_odds": 13.0, "place_probability": 0.1,
         "composite_index": 55.0, "jvan_battle_dm": 60.0},
        {"horse_number": 3, "win_odds": 30.0, "place_probability": 0.05,
         "composite_index": 50.0, "jvan_battle_dm": 50.0},
    ]
    for h in horses:
        for c in SUB_INDEX_COLUMNS:
            h.setdefault(c, None)
    out = rr.score_race(horses, head_count=8)
    # battle DM 順位 1,2 位のみ b_dm (3頭目は順位3)
    assert out[1]["badge_cnt"] == 1
    assert out[2]["badge_cnt"] == 1
    assert out[3]["badge_cnt"] == 0


def test_missing_features_fall_back_to_median() -> None:
    rr = UpsetReranker(_toy_artifact())
    horses = [{"horse_number": 1, "win_odds": 12.0, "place_probability": None,
               "composite_index": None}]
    for c in SUB_INDEX_COLUMNS:
        horses[0].setdefault(c, None)
    out = rr.score_race(horses, head_count=None)
    # 全特徴が median → z=0 → sigmoid(0)=0.5
    assert math.isclose(out[1]["ns"], 0.5, rel_tol=1e-9)
