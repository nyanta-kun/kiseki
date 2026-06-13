"""複勝EVモデル(毎レース1頭の人気薄推奨) serving のユニットテスト。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.betting.place_ev import (
    PlaceEvModel,
    _interp,
    _rank_desc,
    get_place_ev_model,
)

_ARTIFACT = Path(__file__).resolve().parents[1] / "models" / "place_ev_model.v1.json"


def test_interp_clips_and_interpolates() -> None:
    """線形補間は範囲外をクリップし、内側を線形補間する。"""
    xs = [0.0, 0.5, 1.0]
    ys = [0.0, 0.2, 0.9]
    assert _interp(-1.0, xs, ys) == 0.0  # 下限クリップ
    assert _interp(2.0, xs, ys) == 0.9  # 上限クリップ
    assert _interp(0.25, xs, ys) == pytest.approx(0.1)  # 0〜0.5 の中点
    assert _interp(0.75, xs, ys) == pytest.approx(0.55)  # 0.5〜1.0 の中点
    assert _interp(0.5, xs, ys) == pytest.approx(0.2)  # 節点一致


def test_interp_empty_returns_input() -> None:
    """較正写像が空なら入力をそのまま返す。"""
    assert _interp(0.3, [], []) == 0.3


def test_rank_desc_handles_ties_and_none() -> None:
    """降順順位は同値を min 方式、None は None のまま。"""
    r = _rank_desc({1: 10.0, 2: 10.0, 3: 5.0, 4: None})
    assert r[1] == 1.0
    assert r[2] == 1.0  # 同値は同順位(min)
    assert r[3] == 3.0
    assert r[4] is None


def _toy_model() -> PlaceEvModel:
    """単一特徴 log_odds の最小アーティファクトでモデルを組む。"""
    art = {
        "features": ["log_odds"],
        "median": {"log_odds": 2.5},
        "mean": [2.5],
        "scale": [0.5],
        "coef": [-1.0],  # オッズが上がるほど複勝圏確率は下がる
        "intercept": -1.0,
        "floor": 0.2,
        "calibration": {"x": [0.0, 1.0], "y": [0.0, 1.0]},  # 恒等較正
        "odds_impute": [0.0, 0.5, 0.0, 0.0],  # place_odds = win_odds^0.5
        "min_odds": 10.0,
    }
    return PlaceEvModel(art)


def test_impute_place_odds_increasing_in_win_odds() -> None:
    """近似複勝オッズは win_odds に対して単調増加・正値。"""
    m = _toy_model()
    a = m.impute_place_odds(10.0, 12)
    b = m.impute_place_odds(50.0, 12)
    assert 0 < a < b
    # odds_impute=[0,0.5,0,0] → place_odds = exp(0.5*ln(win_odds)) = sqrt(win_odds)
    assert a == pytest.approx(math.sqrt(10.0))


def _race_horses() -> list[dict]:
    """人気薄2頭 + 人気馬1頭の最小レース。"""
    return [
        {"horse_number": 1, "win_odds": 2.0, "composite_index": 80.0,
         "place_probability": 0.6, "win_probability": 0.4, "surface": "芝"},
        {"horse_number": 5, "win_odds": 12.0, "composite_index": 55.0,
         "place_probability": 0.25, "win_probability": 0.08, "surface": "芝"},
        {"horse_number": 8, "win_odds": 60.0, "composite_index": 40.0,
         "place_probability": 0.05, "win_probability": 0.01, "surface": "芝"},
    ]


def test_score_race_only_underdogs() -> None:
    """score_race は人気薄(>=min_odds)のみを返し、人気馬は含まない。"""
    m = _toy_model()
    picks = m.score_race(_race_horses(), head_count=12)
    assert set(picks) == {5, 8}  # 馬番1(2.0倍)は対象外
    for hn, p in picks.items():
        assert p["expected_value"] == pytest.approx(
            p["place_probability"] * p["place_odds_hat"], abs=1e-3
        )


def test_pick_race_respects_floor() -> None:
    """的中率フロアを満たす候補からEV最大を選ぶ。誰も満たさなければ None。"""
    m = _toy_model()
    horses = _race_horses()
    picks = m.score_race(horses, head_count=12)
    # toy では log_odds が大きいほど P が下がる → 馬番8(60倍)は低 P
    pick = m.pick_race(horses, head_count=12)
    if pick is not None:
        assert pick["meets_floor"]
        # フロアを満たす中での EV 最大であること
        eligible = [p for p in picks.values() if p["meets_floor"]]
        assert pick["expected_value"] == max(p["expected_value"] for p in eligible)

    # フロアを 1.0 に上げると誰も満たさず None
    m.floor = 1.0
    assert m.pick_race(horses, head_count=12) is None


def test_pick_race_filters_low_place_odds() -> None:
    """複勝最低オッズ < min_place_odds(2.0) の馬は実オッズ優先で除外する。"""
    m = _toy_model()  # min_place_odds=2.0
    # 単勝12倍でフロアは満たすが、実複勝オッズ1.8倍(<2.0)の人気薄1頭のみ
    horses = [
        {"horse_number": 3, "win_odds": 2.0, "composite_index": 80.0,
         "place_probability": 0.6, "win_probability": 0.4, "surface": "芝"},
        {"horse_number": 7, "win_odds": 12.0, "composite_index": 55.0,
         "place_probability": 0.25, "win_probability": 0.08, "surface": "芝",
         "place_odds": 1.8},
    ]
    # 実複勝1.8倍で除外 → 推奨なし
    assert m.pick_race(horses, head_count=12) is None
    # 実複勝2.5倍なら採用される
    horses[1]["place_odds"] = 2.5
    pick = m.pick_race(horses, head_count=12)
    assert pick is not None and pick["horse_number"] == 7


def test_pick_race_empty_when_no_underdogs() -> None:
    """人気薄がいないレースは None。"""
    m = _toy_model()
    horses = [{"horse_number": 1, "win_odds": 3.0, "composite_index": 70.0,
               "place_probability": 0.5, "surface": "芝"}]
    assert m.pick_race(horses, head_count=10) is None


# ----- 本番アーティファクトが存在する場合の整合性テスト -----

@pytest.mark.skipif(not _ARTIFACT.exists(), reason="artifact 未配置")
def test_production_artifact_loads_and_scores() -> None:
    """本番アーティファクトがロードでき、確率が[0,1]・EVが正であること。"""
    m = get_place_ev_model.__wrapped__()  # lru_cache を避けて都度ロード
    assert m is not None
    assert 0.0 <= m.floor <= 1.0
    assert len(m.coef) == len(m.features)
    picks = m.score_race(_race_horses(), head_count=12)
    for p in picks.values():
        assert 0.0 <= p["place_probability"] <= 1.0
        assert p["place_odds_hat"] > 0
        assert p["expected_value"] >= 0


@pytest.mark.skipif(not _ARTIFACT.exists(), reason="artifact 未配置")
def test_production_calibration_monotonic() -> None:
    """較正写像 x は単調増加(isotonic 由来)。"""
    art = json.loads(_ARTIFACT.read_text())
    xs = art["calibration"]["x"]
    ys = art["calibration"]["y"]
    assert xs == sorted(xs)
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))
