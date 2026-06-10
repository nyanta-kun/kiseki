"""地方 人気薄複勝圏リランカーのユニットテスト。

検証仕様 (memory: upset_place_extraction 地方編):
  軸 = 単勝[10,15) × 非オッズスコア上位1/3 × 外部バッジ(吉馬/netkeiba上位3)。
"""
from __future__ import annotations

import math

from src.indices.chihou_upset import (
    CHIHOU_IDX_COLUMNS,
    CHIHOU_UPSET_BAND_MAX,
    CHIHOU_UPSET_BAND_MIN,
    ChihouUpsetReranker,
)


def _toy_artifact() -> dict:
    """jockey_index のみ係数 1.0 の単純な logistic アーティファクト."""
    feats = ["jockey_index", "kc_sp_rk"]
    return {
        "features": feats,
        "median": {"jockey_index": 50.0, "kc_sp_rk": 5.0},
        "mean": [50.0, 5.0],
        "scale": [10.0, 3.0],
        "coef": [1.0, 0.0],
        "intercept": 0.0,
        "threshold": 0.5,
        "trained_at": "test",
    }


def _rows() -> list[dict]:
    rows = [
        # 本命（10倍未満 → ユニバース外）
        {"horse_number": 1, "win_odds": 2.5, "jockey_index": 70.0,
         "kc_sp": 80.0, "nk_idx": 90.0},
        # 人気薄: jockey 60 → z=1 → sigmoid(1)。kc 2位/nk 2位 → バッジ2
        {"horse_number": 2, "win_odds": 12.0, "jockey_index": 60.0,
         "kc_sp": 70.0, "nk_idx": 80.0},
        # 人気薄: 外部なし → バッジ0
        {"horse_number": 3, "win_odds": 20.0, "jockey_index": 40.0,
         "kc_sp": None, "nk_idx": None},
    ]
    for r in rows:
        for c in CHIHOU_IDX_COLUMNS:
            r.setdefault(c, None)
    return rows


def test_band_constants() -> None:
    assert CHIHOU_UPSET_BAND_MIN == 10.0
    assert CHIHOU_UPSET_BAND_MAX == 15.0


def test_score_race_universe_and_badges() -> None:
    rr = ChihouUpsetReranker(_toy_artifact())
    out = rr.score_race(_rows(), head_count=10)
    assert set(out.keys()) == {2, 3}  # 単勝>=10 のみ
    assert math.isclose(out[2]["ns"], 1.0 / (1.0 + math.exp(-1.0)), rel_tol=1e-9)
    assert out[2]["badge_cnt"] == 2  # kc 2位 + nk 2位
    assert out[3]["badge_cnt"] == 0


def test_axis_tier() -> None:
    rr = ChihouUpsetReranker(_toy_artifact())
    assert rr.axis_tier(12.0, 0.6, 1) == "standard"
    assert rr.axis_tier(12.0, 0.6, 2) == "strong"
    assert rr.axis_tier(15.0, 0.6, 2) is None    # 帯外 [10,15)
    assert rr.axis_tier(9.9, 0.6, 2) is None
    assert rr.axis_tier(12.0, 0.4, 2) is None    # 閾値未満
    assert rr.axis_tier(12.0, 0.6, 0) is None    # バッジなし
    assert rr.axis_tier(None, 0.6, 2) is None


def test_missing_features_fall_back_to_median() -> None:
    rr = ChihouUpsetReranker(_toy_artifact())
    rows = [{"horse_number": 5, "win_odds": 11.0, "kc_sp": None, "nk_idx": None}]
    for c in CHIHOU_IDX_COLUMNS:
        rows[0].setdefault(c, None)
    out = rr.score_race(rows, head_count=None)
    assert math.isclose(out[5]["ns"], 0.5, rel_tol=1e-9)
