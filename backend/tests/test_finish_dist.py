"""`src/indices/finish_dist.py`（能力分布モデル・Phase 2）のテスト。

固定すること:
- σ=1 で Harville と一致する（勝率・上位k着以内率・T3）
- 数値積分が自分自身と整合する（Σ T3 = 1・T3 の周辺 = win_topk_probs）
- シミュレーションが数値積分へ収束する（「事前試行」の収束確認）
- 当てはめが真の μ・σ を復元し、勝率を厳密に保つ。失敗しても NaN を返さない
- 比較腕 B1（2・3着を割り引く Harville）が γ=1 で Harville に戻り、勝率を変えない
"""

from __future__ import annotations

import numpy as np
import pytest

from src.indices.finish_dist import (
    FIT_HARVILLE,
    FIT_OK,
    FIT_RESIDUAL,
    WIN_TOL,
    discounted_harville_top3_tensor,
    fit_random_utility,
    sample_top3,
    top3_tensor,
    win_topk_probs,
)
from src.indices.finish_dist_scoring import harville_top3_tensor, marginals_from_t3


def _win(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).dirichlet(np.ones(n) * 0.7)


def _true(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 1.2, n), np.exp(rng.normal(0.0, 0.5, n))


@pytest.mark.parametrize("n", [3, 5, 12, 18])
@pytest.mark.parametrize("k", [1, 2, 3])
def test_equal_sigma_marginals_match_harville(n: int, k: int) -> None:
    w = _win(n, n)
    win, topk = win_topk_probs(np.log(w), np.ones(n), k)
    m = marginals_from_t3(harville_top3_tensor(w))
    ref = {1: m.win, 2: m.top2, 3: m.top3}[k]
    assert np.max(np.abs(win - w)) < 1e-12
    assert np.max(np.abs(topk - ref)) < 1e-12


@pytest.mark.parametrize("n", [3, 8, 18])
def test_equal_sigma_tensor_matches_harville(n: int) -> None:
    w = _win(n, 100 + n)
    t3 = top3_tensor(np.log(w), np.ones(n))
    ref = harville_top3_tensor(w)
    mask = ref > 1e-8
    assert np.max(np.abs(t3[mask] - ref[mask]) / ref[mask]) < 1e-6


@pytest.mark.parametrize("n", [4, 10, 18])
def test_tensor_is_consistent_with_marginals(n: int) -> None:
    mu, sigma = _true(n, 200 + n)
    t3 = top3_tensor(mu, sigma)
    assert abs(t3.sum() - 1.0) < 1e-7
    idx = np.arange(n)
    assert np.all(t3[idx, idx, :] == 0.0)
    m = marginals_from_t3(t3)
    win, top3 = win_topk_probs(mu, sigma, 3)
    _, top2 = win_topk_probs(mu, sigma, 2)
    assert np.max(np.abs(m.win - win)) < 1e-7
    assert np.max(np.abs(m.top2 - top2)) < 1e-7
    assert np.max(np.abs(m.top3 - top3)) < 1e-7


def test_simulation_converges_to_integral() -> None:
    """100万回の試行で、三連単・三連複の頻度が数値積分の 5 標準誤差以内に入る。"""
    n = 8
    mu, sigma = _true(n, 7)
    t3 = top3_tensor(mu, sigma)
    n_sims = 1_000_000
    orders = sample_top3(mu, sigma, n_sims, np.random.default_rng(11))
    code = orders[:, 0] * n * n + orders[:, 1] * n + orders[:, 2]
    freq = np.bincount(code, minlength=n**3).reshape(n, n, n) / n_sims
    se = np.sqrt(t3 * (1.0 - t3) / n_sims)
    big = t3 > 1e-3
    assert np.all(np.abs(freq[big] - t3[big]) < 5.0 * se[big] + 1e-12)
    # 三連複（組）でも見る
    trio_exact = t3 + t3.transpose(0, 2, 1) + t3.transpose(1, 0, 2)
    trio_exact = trio_exact + t3.transpose(1, 2, 0) + t3.transpose(2, 0, 1) + t3.transpose(2, 1, 0)
    trio_freq = freq + freq.transpose(0, 2, 1) + freq.transpose(1, 0, 2)
    trio_freq = trio_freq + freq.transpose(1, 2, 0) + freq.transpose(2, 0, 1) + freq.transpose(2, 1, 0)
    se3 = np.sqrt(trio_exact * (1.0 - trio_exact) / n_sims)
    big3 = trio_exact > 1e-3
    assert np.all(np.abs(trio_freq[big3] - trio_exact[big3]) < 5.0 * se3[big3] + 1e-12)


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_fit_recovers_true_distribution(seed: int) -> None:
    n = 6 + 3 * seed
    mu, sigma = _true(n, 300 + seed)
    win, place = win_topk_probs(mu, sigma, 3)
    fr = fit_random_utility(win, place, 3)
    assert fr.status == FIT_OK
    assert fr.max_win_resid <= WIN_TOL
    assert np.max(np.abs(fr.win_model - win / win.sum())) <= WIN_TOL
    assert np.max(np.abs(top3_tensor(fr.mu, fr.sigma) - top3_tensor(mu, sigma))) < 1e-6
    # 尺度の不定性を除けば σ も一致する
    s_true = sigma / np.exp(np.log(sigma).mean())
    assert np.max(np.abs(fr.sigma - s_true)) < 1e-3


def test_fit_harville_place_gives_unit_sigma() -> None:
    """複勝の目標が Harville そのものなら σ=1 に戻る（R1 が B0 を特別な場合として含む）。"""
    w = _win(12, 5)
    target = marginals_from_t3(harville_top3_tensor(w)).top3
    fr = fit_random_utility(w, target, 3)
    assert fr.status == FIT_OK
    assert np.max(np.abs(fr.sigma - 1.0)) < 1e-3


def test_fit_k0_is_harville() -> None:
    w = _win(4, 9)
    fr = fit_random_utility(w, np.full(4, np.nan), 0)
    assert fr.status == FIT_HARVILLE
    assert np.all(fr.sigma == 1.0)
    assert fr.max_win_resid <= WIN_TOL


def test_fit_infeasible_target_keeps_win_and_no_nan() -> None:
    """複勝の目標が勝率を下回る馬がいても、クリップして勝率を厳密に保ち NaN を出さない。"""
    w = _win(10, 21)
    place = np.full(10, 0.3)
    place[np.argmax(w)] = w.max() * 0.5  # 勝率より小さい（実現不能）
    fr = fit_random_utility(w, place, 3)
    assert fr.n_clipped >= 1
    # σ が両極端（0.05 と 19）に開くと格子の点数上限で積分の精度が頭打ちになる。
    # そのときは status=residual で返り、採点側は Harville へ落とす（件数を数える）
    assert fr.max_win_resid <= 1e-6
    assert fr.status in (FIT_OK, FIT_RESIDUAL)
    assert np.all(np.isfinite(fr.mu)) and np.all(np.isfinite(fr.sigma))
    assert np.all(np.isfinite(top3_tensor(fr.mu, fr.sigma)))


def test_discounted_harville() -> None:
    w = _win(12, 31)
    assert np.allclose(discounted_harville_top3_tensor(w, 1.0, 1.0), harville_top3_tensor(w), atol=1e-15)
    t3 = discounted_harville_top3_tensor(w, 0.8, 0.7)
    assert abs(t3.sum() - 1.0) < 1e-12
    assert np.max(np.abs(marginals_from_t3(t3).win - w / w.sum())) < 1e-12
