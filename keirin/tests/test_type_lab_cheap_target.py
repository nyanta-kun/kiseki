"""安い決着が見込まれるレースだけ目標を下げる（`apply_cheap_target`・2026-09-29 新設）。

固定すること:
  1. 対象は 7車の A_hit / B_hit だけ（C_hit・9車・三連複には掛けない）
  2. 閾値未満なら `Plan` をそのまま返す（切り替わらない行の買い目が1点も変わらない前提）
  3. `build_with_gate_fallback` を通すと 5倍未満の目が買えるようになり、key は変わらない
  4. 定数を動かすと `rule_version` が割れる
記録: docs/type_lab/cheap_target_2026_09_29.md
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import src.type_lab as TL
from src.type_lab import (
    CHEAP_SHARE_MIN,
    CHEAP_TARGET,
    HIT_BAND_TARGET,
    PLANS,
    apply_cheap_target,
    build_with_gate_fallback,
    cheap_share,
    race_shape,
    rule_version,
)

CARS = list(range(1, 8))
PERMS = list(itertools.permutations(CARS, 3))


def _board(cheap_mass: float):
    """1-2-3 系の6順列に確率 `cheap_mass` を載せ、うち 1-2-3 / 2-1-3 を予測 4倍にする。"""
    probs, odds = {}, {}
    top = [p for p in PERMS if set(p) == {1, 2, 3}]
    rest = [p for p in PERMS if p not in top]
    for p in top:
        probs[p] = cheap_mass / len(top)
    for p in rest:
        probs[p] = (1 - cheap_mass) / len(rest)
    for p in PERMS:
        odds[p] = 0.75 / probs[p]            # 控除 25% の市場
    odds[(1, 2, 3)] = odds[(2, 1, 3)] = 4.0
    return odds, probs


def _shape():
    p3 = {1: .80, 2: .75, 3: .45, 4: .35, 5: .25, 6: .20, 7: .20}
    lg = {1: "a", 2: "a", 3: "a", 4: "b", 5: "b", 6: "c", 7: "d"}
    lp = {1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 1, 7: 1}
    return race_shape(p3, lg, lp, {c: "逃" for c in CARS}, {c: 100 - c for c in CARS},
                      {c: 20.0 for c in CARS}, 2)


def test_share_counts_only_cheap_legs():
    odds, probs = _board(0.6)
    assert cheap_share(odds, probs) == pytest.approx(0.2)   # 4倍の2点ぶん = 0.6 × 2/6


@pytest.mark.parametrize("key", ["A_hit", "B_hit"])
def test_switches_only_at_or_above_threshold(key):
    odds, probs = _board(0.6)
    assert apply_cheap_target(PLANS[key], odds, probs).target == (
        CHEAP_TARGET if cheap_share(odds, probs) >= CHEAP_SHARE_MIN[key] else HIT_BAND_TARGET)
    lo_odds = {p: max(o, 5.0) for p, o in odds.items()}          # 5倍未満が無い
    assert apply_cheap_target(PLANS[key], lo_odds, probs) is PLANS[key]


@pytest.mark.parametrize("key,n", [("C_hit", 7), ("E_hit", 7), ("A_trio", 7), ("A_hit", 9)])
def test_not_applied_outside_scope(key, n):
    odds = {p: 2.0 for p in PERMS}
    probs = {p: 1 / len(PERMS) for p in PERMS}
    assert apply_cheap_target(PLANS[key], odds, probs, n) is PLANS[key]


def test_gate_fallback_buys_cheap_leg_and_keeps_key():
    odds, probs = _board(0.9)
    assert cheap_share(odds, probs) >= CHEAP_SHARE_MIN["A_hit"]
    legs, stakes, used = build_with_gate_fallback(_shape(), PLANS["A_hit"], odds, probs, 7)
    assert used.key == "A_hit" and used.target == CHEAP_TARGET
    assert any(odds[c] < 5.0 for c in stakes)                 # 5万では買えない目が入る
    # 7車以外では掛からない（5倍未満は枠に入らない）
    got9 = build_with_gate_fallback(_shape(), PLANS["A_hit"], odds, probs, 9)
    assert got9 is None or all(odds[c] >= 5.0 for c in got9[1])


def test_rule_version_splits(monkeypatch):
    before = rule_version(7)
    monkeypatch.setattr(TL, "CHEAP_SHARE_MIN", {})
    assert rule_version(7) != before
    monkeypatch.setattr(TL, "CHEAP_SHARE_MIN", dict(CHEAP_SHARE_MIN))
    monkeypatch.setattr(TL, "CHEAP_TARGET", 30_000)
    assert rule_version(7) != before
