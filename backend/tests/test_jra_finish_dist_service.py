"""`services/jra_finish_dist` の検査（予想フロー再設計 Phase 3・計画 §5.5）。

- 配信と前向き採点が同じ関数を通ること（train/serve skew の防止）
- 保存値の丸め（Numeric(6,4)）を読み出し側で正規化し直すこと
- place_slots は実際のフィールドから作ること
- 当てはめが ok でないとき・5頭未満のときは Harville へ落とすこと
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from src.indices.finish_dist import fit_random_utility, top3_tensor, win_topk_probs
from src.indices.finish_dist_scoring import harville_top3_tensor
from src.services.jra_finish_dist import (
    SOURCE_HARVILLE,
    SOURCE_R1,
    build_race_finish_dist,
    cached_race_finish_dist,
    top_combinations,
)

BACKEND = Path(__file__).resolve().parents[1]


def _race(n: int, seed: int = 0, sigma_spread: float = 0.4) -> tuple[list[int], np.ndarray, np.ndarray]:
    """真の μ・σ から勝率と複勝率を作る（当てはめが厳密に再現できる入力）。"""
    rng = np.random.default_rng(seed)
    mu = rng.normal(0, 1, n)
    sigma = np.exp(rng.normal(0, sigma_spread, n))
    k = 3 if n >= 8 else 2
    win, place = win_topk_probs(mu, sigma, k)
    return list(range(1, n + 1)), win, place


def test_r1_matches_inputs() -> None:
    hn, win, place = _race(12)
    fd = build_race_finish_dist(hn, win, place)
    assert fd is not None and fd.source == SOURCE_R1 and fd.place_slots == 3
    np.testing.assert_allclose(fd.win, win / win.sum(), atol=1e-7)
    np.testing.assert_allclose(fd.top3, place, atol=2e-4)
    assert fd.t3.sum() == pytest.approx(1.0, abs=1e-6)
    # 連対率は勝率以上・3着内率以下
    assert np.all(fd.top2 >= fd.win - 1e-12) and np.all(fd.top2 <= fd.top3 + 1e-12)


def test_rounded_inputs_are_renormalized() -> None:
    """Numeric(6,4) に丸めた保存値でも Σ が崩れず、丸める前とほぼ同じ分布になる。"""
    hn, win, place = _race(16, seed=3)
    fd = build_race_finish_dist(hn, win, place)
    fr = build_race_finish_dist(hn, np.round(win, 4), np.round(place, 4))
    assert fd is not None and fr is not None
    assert fr.win.sum() == pytest.approx(1.0, abs=1e-9)
    assert fr.top3.sum() == pytest.approx(3.0, abs=1e-3)
    assert np.abs(fd.t3 - fr.t3).max() < 1e-3


def test_zero_rounded_longshot_is_floored() -> None:
    """丸めで 0.0000 になった大穴があっても落ちない。"""
    hn, win, place = _race(18, seed=5, sigma_spread=0.2)
    w = np.round(win, 4)
    w[np.argmin(w)] = 0.0
    fd = build_race_finish_dist(hn, w, np.round(place, 4))
    assert fd is not None and np.all(np.isfinite(fd.t3)) and np.all(fd.win > 0)


@pytest.mark.parametrize(("n", "k"), [(5, 2), (7, 2), (8, 3), (18, 3)])
def test_place_slots_from_field(n: int, k: int) -> None:
    hn, win, place = _race(n, seed=n)
    fd = build_race_finish_dist(hn, win, place)
    assert fd is not None and fd.place_slots == k


def test_short_field_falls_back_to_harville() -> None:
    """5頭未満は複勝が無いので Harville。"""
    hn = [1, 2, 3, 4]
    win = np.array([0.4, 0.3, 0.2, 0.1])
    fd = build_race_finish_dist(hn, win, win * 2)
    assert fd is not None and fd.place_slots == 0 and fd.source == SOURCE_HARVILLE
    np.testing.assert_allclose(fd.t3, harville_top3_tensor(win), atol=1e-12)


def test_fewer_than_three_returns_none() -> None:
    assert build_race_finish_dist([1, 2], [0.6, 0.4], [1.0, 1.0]) is None


def test_failed_fit_falls_back_to_harville() -> None:
    """複勝が単勝より小さい（実現できない目標）なら当てはめは ok にならず B0。"""
    hn = list(range(1, 11))
    win = np.full(10, 0.1)
    place = np.full(10, 0.3)
    place[0] = 0.01  # 勝率 0.1 より小さい 3着内率
    place = place * 3 / place.sum()
    fd = build_race_finish_dist(hn, win, place)
    assert fd is not None
    assert fd.fit_status != "ok" and fd.source == SOURCE_HARVILLE


def test_serve_and_forward_share_one_path() -> None:
    """同じ入力から、配信用のキャッシュ経由と前向き採点の直呼びが同じ T3 を返す。"""
    hn, win, place = _race(14, seed=11)
    w4, p4 = np.round(win, 4), np.round(place, 4)
    direct = build_race_finish_dist(hn, w4, p4)
    served = cached_race_finish_dist(999_001, hn, w4.tolist(), p4.tolist())
    assert direct is not None and served is not None
    np.testing.assert_array_equal(direct.t3, served.t3)
    # 当てはめを手で呼んだ結果とも一致する（中で別の前処理をしていない）
    w = np.clip(w4, 1e-6, None)
    w = w / w.sum()
    p = np.clip(p4, 1e-6, None)
    p = p * 3 / p.sum()
    fr = fit_random_utility(w, p, 3)
    np.testing.assert_allclose(direct.t3, top3_tensor(fr.mu, fr.sigma), atol=1e-12)


def test_forward_script_and_api_use_service() -> None:
    """前向き採点スクリプトと API が `build_race_finish_dist` 系だけを通っている。"""
    for rel, name in (
        ("scripts/jra_finish_dist_forward.py", "build_race_finish_dist"),
        ("src/api/races.py", "cached_race_finish_dist"),
    ):
        tree = ast.parse((BACKEND / rel).read_text())
        imported = {
            a.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("services.jra_finish_dist")
            for a in node.names
        }
        assert name in imported, f"{rel} が services.jra_finish_dist.{name} を使っていない"
        # 当てはめを直接呼んで別経路を作っていない
        direct = {
            a.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("indices.finish_dist")
            for a in node.names
        }
        assert "fit_random_utility" not in direct and "top3_tensor" not in direct, rel


def test_top_combinations_order_and_sum() -> None:
    hn, win, place = _race(10, seed=2)
    fd = build_race_finish_dist(hn, win, place)
    assert fd is not None
    trio = top_combinations(fd, "trio", 1000)
    assert len(trio) == 120
    assert sum(p for _, p in trio) == pytest.approx(1.0, abs=1e-6)
    assert all(trio[i][1] >= trio[i + 1][1] for i in range(len(trio) - 1))
    assert all(list(c) == sorted(c) for c, _ in trio)
    quin = top_combinations(fd, "quinella", 1000)
    assert sum(p for _, p in quin) == pytest.approx(1.0, abs=1e-6)
    wide = top_combinations(fd, "wide", 1000)
    assert sum(p for _, p in wide) == pytest.approx(3.0, abs=1e-5)
    with pytest.raises(ValueError):
        top_combinations(fd, "exacta", 3)
