"""入稿案API（期待値・最低/最高払戻）の計算を固定する（2026-08-11）。

## 守ること

1. オッズが**1つでも欠けたら**最低/最高払戻は None
   （一部だけで計算すると、欠けた点が最安だった場合に最低払戻を実際より
     高く見せる＝確認の役に立たない）
2. 期待値は三連単（着順あり）では出さない（この確率モデルでは扱えない）
3. 三連複の確率はレース内で正規化される
"""
from __future__ import annotations

import pytest

from src.api.keirin_router import (
    _expected_value,
    _payout_range,
    _trio_probabilities,
)

_TOP3 = {1: 70.0, 2: 60.0, 3: 50.0, 4: 40.0, 5: 30.0, 6: 25.0, 7: 20.0}


def _lines(*specs):
    return [{"bet_type": "3連複", "combo": c, "stake": s, "odds": o} for c, s, o in specs]


def test_payout_range_basic():
    lo, hi = _payout_range(_lines(("1=2=3", 600, 5.0), ("1=2=4", 400, 9.0)))
    assert lo == pytest.approx(3000.0)
    assert hi == pytest.approx(3600.0)


@pytest.mark.parametrize("bad", [None, 0])
def test_payout_range_none_when_any_odds_missing(bad):
    """1点でも欠けたら None。部分計算は最低払戻を過大に見せる。"""
    lo, hi = _payout_range(_lines(("1=2=3", 600, 5.0), ("1=2=4", 400, bad)))
    assert lo is None and hi is None


def test_payout_range_empty():
    assert _payout_range([]) == (None, None)


def test_trio_probabilities_sum_to_one():
    probs = _trio_probabilities(_TOP3)
    assert len(probs) == 35  # 7C3
    assert sum(probs.values()) == pytest.approx(1.0)
    # 3着内率が高い3車の組が最大になる
    assert max(probs, key=probs.get) == frozenset({1, 2, 3})


def test_trio_probabilities_needs_three_cars():
    assert _trio_probabilities({1: 50.0, 2: 40.0}) == {}
    assert _trio_probabilities({}) == {}


def test_expected_value_is_ratio_of_stake():
    """期待値は投資に対する見込み回収率。1.0 で収支トントン。"""
    lines = _lines(("1=2=3", 600, 5.0), ("1=2=4", 400, 9.0))
    ev = _expected_value(lines, _TOP3)
    probs = _trio_probabilities(_TOP3)
    want = (probs[frozenset({1, 2, 3})] * 600 * 5.0
            + probs[frozenset({1, 2, 4})] * 400 * 9.0) / 1000
    assert ev == pytest.approx(want)


def test_expected_value_none_for_trifecta():
    """三連単（着順あり）は扱えないので None。無理に数字を出さない。"""
    lines = [{"bet_type": "3連単", "combo": "1-2-3", "stake": 1000, "odds": 30.0}]
    assert _expected_value(lines, _TOP3) is None


def test_expected_value_none_when_odds_missing():
    lines = _lines(("1=2=3", 600, 5.0), ("1=2=4", 400, None))
    assert _expected_value(lines, _TOP3) is None


def test_expected_value_none_without_probabilities():
    lines = _lines(("1=2=3", 1000, 5.0))
    assert _expected_value(lines, {1: 50.0, 2: 40.0}) is None


def test_expected_value_unknown_combo_is_none():
    """買い目に出走していない車が含まれていたら黙って0扱いにしない。"""
    lines = _lines(("1=2=9", 1000, 5.0))
    assert _expected_value(lines, _TOP3) is None
