"""商品としての「最低払戻」の正本（`keirin_payout_floor`）。

🔴 検査したいのは**「1点あたりの分位を k点の最小へ流用していない」**こと。
   流用すると点数が増えるほど甘くなり、「下側25%分位」と名乗った額を
   5点の商品では4回に3回割る（2026-08-29 まで実際にそうだった）。
"""
from __future__ import annotations

import pytest

from src.services.keirin_payout_floor import (
    FLOOR_RATIO_BY_POINTS,
    bet_kind_of,
    floor_ratio,
    min_payout_floor,
)


def _line(stake, odds, low=None, bet_type="3連複", source="predicted"):
    return {"stake": stake, "odds": odds, "bet_type": bet_type,
            "odds_source": source,
            "odds_low": odds * 0.8428 if low is None else low}


@pytest.mark.parametrize("kind", ("trio", "trifecta"))
def test_点数が増えるほど下がる(kind):
    """順序統計量なので単調に下がる。等号があってはいけない。"""
    ks = sorted(FLOOR_RATIO_BY_POINTS[kind])
    vals = [FLOOR_RATIO_BY_POINTS[kind][k] for k in ks]
    assert vals == sorted(vals, reverse=True)
    assert all(a > b for a, b in zip(vals, vals[1:])), "同じ値が並んでいます"
    assert 0 < vals[-1] < vals[0] <= 1.0


def test_三連単のほうが深く食い込む():
    """三連単は予測のばらつきが大きい（±2倍以内 80.6% ↔ 三連複 91.6%）。"""
    for k in FLOOR_RATIO_BY_POINTS["trifecta"]:
        assert FLOOR_RATIO_BY_POINTS["trifecta"][k] < FLOOR_RATIO_BY_POINTS["trio"][k]


def test_表に無い点数でも例外にしない():
    assert floor_ratio(99) == FLOOR_RATIO_BY_POINTS["trio"][14]
    with pytest.raises(ValueError):
        floor_ratio(0)


def test_券種は三連単側へ倒す():
    assert bet_kind_of([{"bet_type": "3連複"}]) == "trio"
    assert bet_kind_of([{"bet_type": "3連単"}]) == "trifecta"
    assert bet_kind_of([{"bet_type": "3連複"}, {"bet_type": "3連単"}]) == "trifecta"


def test_予測オッズなら計画の最小に点数別の倍率を掛ける():
    lines = [_line(2000, 10.0), _line(3000, 8.0), _line(5000, 6.0)]
    plan = min(2000 * 10.0, 3000 * 8.0, 5000 * 6.0)      # = 20,000
    assert min_payout_floor(lines) == pytest.approx(plan * floor_ratio(3, "trio"))


def test_1点あたりの分位をそのまま使っていない():
    """🔴 旧実装（min(賭け金 × odds_low)）より必ず小さくなる。

    ここが等しくなったら「点数を見ない」実装へ戻っている。
    """
    for k in (3, 5, 8):
        lines = [_line(10000 // k, 5.0 + i) for i in range(k)]
        old = min(x["stake"] * x["odds_low"] for x in lines)
        assert min_payout_floor(lines) < old * 0.98, f"{k}点で旧実装と変わりません"


def test_板が混ざる古い記録は従来どおり():
    """板は買う帯で系統的に高いので、k 補正を掛けると楽観を残したまま緩む。"""
    lines = [_line(5000, 10.0, source="board"), _line(5000, 8.0, source="board")]
    assert min_payout_floor(lines) == pytest.approx(
        min(x["stake"] * x["odds_low"] for x in lines))


def test_odds_lowが欠けたらNone():
    assert min_payout_floor([]) is None
    assert min_payout_floor([_line(5000, 10.0, low=None) | {"odds_low": None}]) is None
