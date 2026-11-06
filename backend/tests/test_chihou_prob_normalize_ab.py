"""`scripts/chihou_prob_normalize_ab.py` の純関数の検査（予想フロー再設計 Phase 1）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.chihou_prob_normalize_ab import score_races


def _race(rid: int, pw: list[float], pp: list[float], fp: list[int], ab: list[int] | None = None) -> pd.DataFrame:
    n = len(pw)
    return pd.DataFrame(
        {
            "race_id": rid,
            "date": "20260901",
            "horse_number": range(1, n + 1),
            "win_probability": pw,
            "place_probability": pp,
            "finish_position": fp,
            "abnormality_code": ab or [0] * n,
        }
    )


def test_win_mll_delta_is_log_of_sum_identically() -> None:
    """単勝の多項対数損失の差は結果に依らず log(Σp)（判定に使えない理由）。"""
    pw = [0.5, 0.3, 0.2, 0.2, 0.1]
    pp = [0.8, 0.7, 0.6, 0.5, 0.4]
    for winner in range(5):
        fp = [99] * 5
        fp[winner] = 1
        r = score_races(_race(1, pw, pp, fp)).iloc[0]
        assert r["win_mll_norm"] - r["win_mll_raw"] == pytest.approx(np.log(sum(pw)))


def test_scratched_horses_are_excluded_before_normalizing() -> None:
    pw = [0.5, 0.5, 0.25, 0.25]
    pp = [0.9, 0.9, 0.6, 0.6]
    df = _race(1, pw, pp, [1, 0, 2, 3], ab=[0, 1, 0, 0])
    r = score_races(df).iloc[0]
    assert r["n"] == 3
    assert r["win_sum"] == pytest.approx(1.0)
    # 正規化後の 1着馬の確率 = 0.5 / 1.0
    assert r["win_mll_norm"] == pytest.approx(-np.log(0.5))


def test_place_normalized_to_three_and_clipped() -> None:
    pw = [0.6, 0.2, 0.1, 0.05, 0.05]
    pp = [0.99, 0.2, 0.2, 0.1, 0.1]  # Σ=1.59 → ×3/1.59 で1頭目が1を超える
    r = score_races(_race(1, pw, pp, [1, 2, 3, 4, 5])).iloc[0]
    assert r["n_place_clipped"] == 1
    assert np.isfinite(r["top3_ll_norm"])
