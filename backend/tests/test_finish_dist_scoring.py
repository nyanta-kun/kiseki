"""`src/indices/finish_dist_scoring.py` の検査（予想フロー再設計 Wave 0c）。

既知値の再現（既存の Harville 実装・全順列の数え上げ）と、
シミュレーションが厳密値へ収束することを固定する。
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from src.betting.finish_order import _harville_joint
from src.indices.composite import CompositeIndexCalculator
from src.indices.finish_dist_scoring import (
    STATUS_DEAD_HEAT,
    STATUS_NO_WINNER,
    STATUS_OK,
    STATUS_SHORT_FIELD,
    harville_top3_tensor,
    marginals_from_t3,
    paired_bootstrap_ci,
    realized_top3,
    sample_plackett_luce_top3,
    score_race,
    top3_tensor_from_orders,
)


def _rand_pwin(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    p = rng.dirichlet(np.full(n, 0.7))
    return p


# ---------------------------------------------------------------------------
# 既知値の再現
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n,seed", [(3, 1), (6, 2), (12, 3), (18, 4)])
def test_harville_tensor_matches_finish_order_joint(n: int, seed: int) -> None:
    """全ての順序つき3頭組で `betting/finish_order._harville_joint` と一致する。"""
    p = _rand_pwin(n, seed)
    t3 = harville_top3_tensor(p)
    wp = {i: float(p[i]) for i in range(n)}
    for a, b, c in itertools.permutations(range(n), 3):
        assert t3[a, b, c] == pytest.approx(_harville_joint(wp, (a, b, c)), rel=1e-10, abs=1e-15)
    assert t3.sum() == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("n,seed", [(5, 11), (8, 12), (16, 13)])
def test_top3_marginal_matches_production_harville_place(n: int, seed: int) -> None:
    """複勝確率が本番 `_harville_place_probs` と一致する（v27 の複勝確率）。

    本番は 5〜7頭立てを2着以内で計算する（`place_slots=2`）ので、比べる先を頭数で選ぶ。
    """
    p = _rand_pwin(n, seed)
    m = marginals_from_t3(harville_top3_tensor(p))
    prod = np.asarray(CompositeIndexCalculator._harville_place_probs(p.tolist()), dtype=float)
    ours = m.top3 if n >= 8 else m.top2
    np.testing.assert_allclose(ours, prod, rtol=1e-9, atol=1e-12)


def test_marginals_match_full_permutation_enumeration() -> None:
    """全着順（5! 通り）を数え上げた値と、T3 から導いた全券種が一致する。"""
    n = 5
    p = _rand_pwin(n, 21)
    m = marginals_from_t3(harville_top3_tensor(p))
    wp = {i: float(p[i]) for i in range(n)}

    win = np.zeros(n)
    top2 = np.zeros(n)
    top3 = np.zeros(n)
    quin = np.zeros((n, n))
    wide = np.zeros((n, n))
    trio: dict[frozenset[int], float] = {}
    for perm in itertools.permutations(range(n)):
        pr = _harville_joint(wp, perm)
        win[perm[0]] += pr
        for h in perm[:2]:
            top2[h] += pr
        for h in perm[:3]:
            top3[h] += pr
        a, b = perm[0], perm[1]
        quin[a, b] += pr
        quin[b, a] += pr
        for x, y in itertools.combinations(perm[:3], 2):
            wide[x, y] += pr
            wide[y, x] += pr
        key = frozenset(perm[:3])
        trio[key] = trio.get(key, 0.0) + pr

    np.testing.assert_allclose(m.win, win, atol=1e-12)
    np.testing.assert_allclose(m.top2, top2, atol=1e-12)
    np.testing.assert_allclose(m.top3, top3, atol=1e-12)
    np.testing.assert_allclose(m.quinella, quin, atol=1e-12)
    np.testing.assert_allclose(m.wide, wide, atol=1e-12)
    for key, pr in trio.items():
        a, b, c = sorted(key)
        assert m.trio[a, b, c] == pytest.approx(pr, abs=1e-12)
    # 券種ごとの合計（連対 2・複勝 3・ワイド 3 組）
    assert m.top2.sum() == pytest.approx(2.0)
    assert m.top3.sum() == pytest.approx(3.0)
    assert m.wide[np.triu_indices(n, 1)].sum() == pytest.approx(3.0)
    assert m.quinella[np.triu_indices(n, 1)].sum() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# シミュレーションの収束（合格基準: 10万回・100万回で厳密値へ近づく）
# ---------------------------------------------------------------------------


def test_simulation_converges_to_exact_harville() -> None:
    """Plackett-Luce のシミュレーションが厳密な T3 へ収束する。

    誤差は 1/√S で縮むので、10万回 → 100万回で約 1/√10 になるはず。
    """
    n = 14
    p = _rand_pwin(n, 31)
    exact = harville_top3_tensor(p)
    rng = np.random.default_rng(7)
    errs = {}
    for s in (100_000, 1_000_000):
        emp = top3_tensor_from_orders(sample_plackett_luce_top3(p, s, rng), n)
        errs[s] = float(np.abs(emp - exact).max())
        # 単勝・複勝の周辺確率は 10万回でも 3σ 以内
        m_emp, m_ex = marginals_from_t3(emp), marginals_from_t3(exact)
        sd = np.sqrt(m_ex.top3 * (1 - m_ex.top3) / s)
        assert np.all(np.abs(m_emp.top3 - m_ex.top3) <= 5 * sd + 1e-12)
    assert errs[1_000_000] < errs[100_000]
    assert errs[1_000_000] < 0.6 * errs[100_000]


def test_simulated_scores_converge_but_exotics_are_noisy_at_100k() -> None:
    """実現組の対数損失はシミュレーションで厳密値へ近づく。

    三連複の実現組がまれなとき、10万回では誤差が検出したい差（0.005 nat）より大きい。
    これが「採点は厳密な T3 で行う」理由（計画 §3 Wave 0c）。
    """
    n = 16
    p = _rand_pwin(n, 41)
    exact = harville_top3_tensor(p)
    m = marginals_from_t3(exact)
    # まれな三連複の組（確率が 0.1% 前後のもの）を実現組にする
    iu = [(a, b, c) for a, b, c in itertools.combinations(range(n), 3)]
    a, b, c = min(iu, key=lambda t: abs(m.trio[t] - 1e-3))
    fp = np.full(n, 9.0)
    fp[a], fp[b], fp[c] = 1, 2, 3
    ref = score_race(exact, fp, 3)["trio_ll"]

    rng = np.random.default_rng(99)
    dev = {}
    for s in (100_000, 1_000_000):
        emp = top3_tensor_from_orders(sample_plackett_luce_top3(p, s, rng), n)
        dev[s] = abs(score_race(emp, fp, 3)["trio_ll"] - ref)
    assert dev[1_000_000] < 0.05
    # 10万回の相対誤差の期待値は 1/√(S·p) ≒ 0.1 → 0.005 nat を確実に超えうる規模
    assert 1.0 / np.sqrt(100_000 * m.trio[a, b, c]) > 0.005


# ---------------------------------------------------------------------------
# 1 レースの採点と母集団の規則
# ---------------------------------------------------------------------------


def test_score_race_values_by_hand() -> None:
    p = np.array([0.5, 0.3, 0.15, 0.05])
    t3 = harville_top3_tensor(p)
    fp = np.array([2, 1, 3, 4])
    r = score_race(t3, fp, place_slots=2)
    assert r["status"] == STATUS_OK
    assert r["win_ll"] == pytest.approx(-np.log(0.3))
    # 馬単 1→0: 0.3 * 0.5/0.7
    assert r["exacta_ll"] == pytest.approx(-np.log(0.3 * 0.5 / 0.7))
    m = marginals_from_t3(t3)
    assert r["quinella_ll"] == pytest.approx(-np.log(m.quinella[0, 1]))
    assert r["trio_ll"] == pytest.approx(-np.log(m.trio[0, 1, 2]))
    # place_slots=2 のときは T3 の 2着以内の確率で採点する
    y = np.array([1, 1, 0, 0], dtype=float)
    q = m.top2
    exp = np.mean(-(y * np.log(q) + (1 - y) * np.log(1 - q)))
    assert r["place_ll"] == pytest.approx(exp)


def test_place_probs_override_and_matches_place_head_ab_definition() -> None:
    """独立複勝ヘッドの確率を渡すと、`_place_ll_per_race` と同じ式で採点する。"""
    p = _rand_pwin(10, 51)
    t3 = harville_top3_tensor(p)
    fp = np.array([3, 1, 2, 4, 5, 6, 7, 8, np.nan, 0])
    pp = np.clip(np.linspace(0.05, 0.6, 10), 1e-9, 1 - 1e-9)
    r = score_race(t3, fp, 3, place_probs=pp)
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=float)
    exp = np.mean(-(y * np.log(pp) + (1 - y) * np.log(1 - pp)))
    assert r["place_ll"] == pytest.approx(exp)


def test_wide_has_three_winning_pairs() -> None:
    p = _rand_pwin(6, 61)
    t3 = harville_top3_tensor(p)
    fp = np.array([4, 1, 5, 2, 6, 3])
    m = marginals_from_t3(t3)
    r = score_race(t3, fp, 3)
    iu = np.triu_indices(6, 1)
    in3 = np.isin(np.arange(6), [1, 3, 5])
    y = (in3[:, None] & in3[None, :])[iu].astype(float)
    assert y.sum() == 3
    q = m.wide[iu]
    assert r["wide_ll"] == pytest.approx(np.mean(-(y * np.log(q) + (1 - y) * np.log(1 - q))))


@pytest.mark.parametrize(
    "fp,status",
    [
        ([1, 1, 3, 4, 5], STATUS_DEAD_HEAT),  # 1着同着
        ([1, 2, 2, 4, 5], STATUS_DEAD_HEAT),  # 2着同着
        ([1, 2, 0, 0, 0], STATUS_SHORT_FIELD),  # 3着が居ない（中止）
    ],
)
def test_realized_top3_rules(fp: list[int], status: str) -> None:
    assert realized_top3(np.array(fp, dtype=float))[0] == status


def test_dead_heat_keeps_win_but_drops_combos() -> None:
    p = _rand_pwin(5, 71)
    t3 = harville_top3_tensor(p)
    r = score_race(t3, np.array([1, 2, 2, 4, 5], dtype=float), 3)
    assert r["status"] == STATUS_DEAD_HEAT
    assert np.isfinite(r["win_ll"]) and np.isfinite(r["place_ll"])
    for k in ("quinella_ll", "trio_ll", "wide_ll", "top2_ll"):
        assert np.isnan(r[k])
    r2 = score_race(t3, np.array([1, 1, 3, 4, 5], dtype=float), 3)
    assert r2["status"] == STATUS_NO_WINNER
    assert np.isnan(r2["win_ll"])


def test_zero_probability_combo_is_floored_not_infinite() -> None:
    """シミュレーションで一度も出なかった組でも無限大にならない（EPS で下限）。"""
    n = 6
    t3 = np.zeros((n, n, n))
    t3[0, 1, 2] = 1.0
    r = score_race(t3, np.array([3, 4, 5, 1, 2, 6], dtype=float), 3)
    assert np.isfinite(r["trio_ll"]) and r["trio_ll"] == pytest.approx(-np.log(1e-9))


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        score_race(harville_top3_tensor(_rand_pwin(5, 1)), np.arange(1, 7), 3)


# ---------------------------------------------------------------------------
# 対応差の信頼区間
# ---------------------------------------------------------------------------


def test_paired_bootstrap_ci_is_deterministic_and_ignores_nan() -> None:
    rng = np.random.default_rng(0)
    d = rng.normal(-0.01, 0.1, 3000)
    d_nan = np.concatenate([d, [np.nan] * 10])
    a = paired_bootstrap_ci(d_nan, n_boot=500)
    b = paired_bootstrap_ci(d_nan, n_boot=500)
    assert a == b
    assert a["n_races"] == 3000
    assert a["ci"][0] < a["mean"] < a["ci"][1]
    # 理論上の標準誤差 0.1/√3000 ≒ 0.0018 → 95%CI の幅 ≒ 0.0072
    assert 0.005 < a["ci"][1] - a["ci"][0] < 0.010
    assert paired_bootstrap_ci([np.nan])["n_races"] == 0
