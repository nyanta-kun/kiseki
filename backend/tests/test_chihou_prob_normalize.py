"""`src/indices/chihou_prob_normalize.py` の検査（予想フロー再設計 Phase 1）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indices.chihou_prob_normalize import normalize_top3, normalize_win, scale_to_sum


def test_win_sums_to_one() -> None:
    out = normalize_win({1: 0.6, 2: 0.4, 3: 0.3})
    assert sum(v for v in out.values() if v is not None) == pytest.approx(1.0)
    assert out[1] == pytest.approx(0.6 / 1.3)


def test_top3_sums_to_three_and_clips_without_renormalizing() -> None:
    out = normalize_top3({1: 0.95, 2: 0.2, 3: 0.2, 4: 0.1})
    assert out[1] == 1.0
    # 再正規化しないので合計は 3 を下回る
    assert sum(v for v in out.values() if v is not None) < 3.0
    assert out[2] == pytest.approx(0.2 * 3 / 1.45)


def test_missing_values_stay_missing_and_are_ignored() -> None:
    out = normalize_win({1: 0.5, 2: None, 3: 0.5})
    assert out[2] is None
    assert out[1] == pytest.approx(0.5)


def test_all_zero_or_missing_is_returned_as_is() -> None:
    assert scale_to_sum({1: 0.0, 2: 0.0}, 1.0) == {1: 0.0, 2: 0.0}
    assert scale_to_sum({1: None}, 1.0) == {1: None}


def test_matches_measurement_script_definition() -> None:
    """測定スクリプトと同じ値になる（測ったものと配信するものを一致させる）。"""
    from scripts.chihou_prob_normalize_ab import EPS

    rng = np.random.default_rng(0)
    pw = rng.uniform(0.01, 0.4, 12)
    pp = rng.uniform(0.05, 0.9, 12)
    ours_w = normalize_win(dict(enumerate(pw)))
    ours_p = normalize_top3(dict(enumerate(pp)))
    ref_w = pw / pw.sum()
    ref_p = np.clip(pp * 3.0 / pp.sum(), EPS, 1 - EPS)
    np.testing.assert_allclose([ours_w[i] for i in range(12)], ref_w)
    np.testing.assert_allclose([ours_p[i] for i in range(12)], ref_p, atol=1e-8)
    assert isinstance(pd.Series(ours_w).sum(), float)


def test_normalize_field_excludes_scratched_horses() -> None:
    from src.indices.chihou_prob_normalize import normalize_field

    w, p = normalize_field({1: 0.5, 2: 0.5, 3: 0.25}, {1: 0.9, 2: 0.9, 3: 0.6}, excluded={2})
    assert w[2] is None and p[2] is None
    assert w[1] == pytest.approx(0.5 / 0.75)
    assert p[1] == pytest.approx(min(1.0, 0.9 * 3 / 1.5))
