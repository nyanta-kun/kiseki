"""`services/chihou_finish_dist` の検査（予想フロー再設計・地方 Phase 2・計画 §5.6）。"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from src.indices.finish_dist import win_topk_probs
from src.indices.finish_dist_scoring import harville_top3_tensor
from src.services.chihou_finish_dist import SOURCE_HARVILLE, SOURCE_R1, build_race_finish_dist, fit_k

BACKEND = Path(__file__).resolve().parents[1]


def _race(n: int, seed: int = 0) -> tuple[list[int], np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    mu = rng.normal(0, 1, n)
    sigma = np.exp(rng.normal(0, 0.4, n))
    win, top3 = win_topk_probs(mu, sigma, fit_k(n))
    return list(range(1, n + 1)), win, top3


@pytest.mark.parametrize(("n", "k"), [(3, 0), (4, 3), (5, 3), (7, 3), (8, 3), (16, 3)])
def test_fit_k_is_top3_regardless_of_payout(n: int, k: int) -> None:
    """地方のヘッドは「3着以内」なので、5〜7頭立てでも k=3（中央の払戻ルールの 2 ではない）。"""
    assert fit_k(n) == k


def test_small_field_fits_top3_target() -> None:
    hn, win, top3 = _race(6, seed=1)
    fd = build_race_finish_dist(hn, win, top3)
    assert fd is not None and fd.source == SOURCE_R1 and fd.k == 3
    np.testing.assert_allclose(fd.top3, top3, atol=2e-4)


def test_raw_sums_are_rescaled() -> None:
    """保存値は合計が揃っていない（勝率 平均 1.08・3着内率 3.17）。中で揃えてから当てはめる。"""
    hn, win, top3 = _race(12, seed=2)
    fd = build_race_finish_dist(hn, win * 1.08, top3 * 1.05)
    assert fd is not None and fd.source == SOURCE_R1
    assert fd.win.sum() == pytest.approx(1.0, abs=1e-9)
    assert fd.top3.sum() == pytest.approx(3.0, abs=1e-3)


def test_tiny_field_is_harville() -> None:
    fd = build_race_finish_dist([1, 2, 3], [0.5, 0.3, 0.2], [1.0, 1.0, 1.0])
    assert fd is not None and fd.k == 0 and fd.source == SOURCE_HARVILLE
    np.testing.assert_allclose(fd.t3, harville_top3_tensor(np.array([0.5, 0.3, 0.2])), atol=1e-12)


def test_ab_script_uses_chihou_service_not_jra() -> None:
    """採点スクリプトは地方の入口を通す。中央の入口（k=place_slots）を通すと小頭数が静かに間違う。"""
    tree = ast.parse((BACKEND / "scripts/chihou_finish_dist_ab.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "src.services.chihou_finish_dist" in mods
    assert "src.services.jra_finish_dist" not in mods
