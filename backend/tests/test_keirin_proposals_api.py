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


# ── 下振れ側の最低払戻（2026-08-16 追加）─────────────────────────────
#
# 🔴 これが要る理由（実測が起点）: `min_payout` の元になる `odds` は入稿時点の
#    板が最優先だが、**朝の板は買い目の帯で確定までに大きく下がる**。
#    実入稿 705点を確定オッズと突合すると 中央 確定/表示 = 0.860・
#    45.0% が 0.8倍未満（7C は 中央 0.651・64.3%）。
#    つまり従来の最低払戻は**当たったとき実際より高い額を約束していた**。
#    keirin 側が `odds_low`（予測の整合板 × 下側25%分位）を記録するので、
#    ガミ判定はそちらを優先する。

from src.api.keirin_router import _min_payout_low  # noqa: E402


def _lines_low(*specs):
    """(combo, stake, odds, odds_low) の並び。"""
    return [{"bet_type": "3連複", "combo": c, "stake": s, "odds": o, "odds_low": lo}
            for c, s, o, lo in specs]


def test_min_payout_low_uses_the_conservative_odds():
    lines = _lines_low(("1=2=3", 2500, 5.0, 4.2), ("1=2=4", 2500, 9.0, 7.6))
    assert _min_payout_low(lines) == pytest.approx(2500 * 4.2)
    # 板由来の最低払戻より必ず低い＝楽観側へ倒れない
    assert _min_payout_low(lines) < _payout_range(lines)[0]


@pytest.mark.parametrize("bad", [None, 0])
def test_min_payout_low_is_none_when_any_point_lacks_it(bad):
    """🔴 一部だけで計算しない。欠けた点が最安なら下限を高く見せることになる
    （`_payout_range` と同じ規約）。"""
    assert _min_payout_low(_lines_low(("1=2=3", 2500, 5.0, 4.2),
                                      ("1=2=4", 2500, 9.0, bad))) is None


def test_min_payout_low_is_none_for_old_records():
    """`odds_low` を持たない記録（三連単・2026-08-16 以前の入稿）は None。

    この場合、呼び出し側は従来どおり `min_payout` でガミ判定する。
    """
    assert _min_payout_low(_lines(("1=2=3", 2500, 5.0))) is None
    assert _min_payout_low([]) is None
