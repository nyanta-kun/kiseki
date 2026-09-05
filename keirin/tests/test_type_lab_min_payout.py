"""最低2倍保証と信頼度傾斜（`MIN_PAYOUT_MULT`）を固定する（2026-09-05）。

ユーザー要望「最低2倍を担保しながら、買い目に対しての信頼度を得点化し、
信頼度が高いところから払い戻しが多くなる様に傾斜配分する」。

ここで固定するのは3点:

  1. 🔴 **床が置けないレースを見送りにしない**（変更前の配分へ落とす）。
     母集団を静かに削るのが一番たちが悪い（実測 8.3〜8.5%）。
  2. 🔴 **穴狙い・看板には掛けない**（`A_ana` は実測 ROI −12.9pt）。
  3. 床は**予算×2.0 以上**を厳密に満たす（`ceil` を `int` にすると割る）。
"""
from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from src.type_lab import (
    ALLOC_BEFORE_MIN_PAYOUT, BUDGET, DEFAULT_FLOOR_MULT, MIN_PAYOUT_MULT,
    PLANS, RaceShape, alloc_fallback, allocate, build_with_gate_fallback,
    min_expected_payout,
)

HIT = ("A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit")
KEEP = ("A_ana", "A_trio", "A_pay", "F_pay", "F_sign")
PERMS = list(itertools.permutations(range(1, 8), 3))


def test_min_payout_mult_is_two():
    assert MIN_PAYOUT_MULT == 2.0


@pytest.mark.parametrize("key", HIT)
def test_hit_plans_guarantee_two_times(key):
    """🔴 当たれば最低2倍。当たる回数を売る6商品に掛ける。"""
    p = PLANS[key]
    assert p.alloc == "conf"
    assert p.floor_mult == MIN_PAYOUT_MULT


@pytest.mark.parametrize("key", KEEP)
def test_longshot_and_signboard_plans_are_untouched(key):
    """⚠️ `A_ana` は実測で ROI 78.6 → 65.7。高配当への傾斜が床に食われる。"""
    assert PLANS[key].floor_mult == DEFAULT_FLOOR_MULT
    assert alloc_fallback(PLANS[key]) is None


def test_floor_is_exact_at_two_times():
    """予測 17.0倍・8点なら floor は ceil(20000/1700) = 12単位（切り捨てなら11）。

    12単位 = 1,200円 → 想定払戻 20,400円 >= 予算×2.0。11単位なら 18,700円で割る。
    """
    legs = [(1, 2, c) for c in range(3, 8)] + [(1, 3, c) for c in range(4, 7)]
    assert len(legs) == 8
    odds = {c: 17.0 for c in legs}
    prob = {c: (1.0 if i == 0 else 1e-6) for i, c in enumerate(legs)}
    st = allocate(legs, odds, prob, PLANS["A_hit"])
    assert st is not None
    assert sum(st.values()) == BUDGET
    assert min_expected_payout(st, odds) >= BUDGET * MIN_PAYOUT_MULT


def test_confidence_gets_a_bigger_payout():
    """🔴 床のうえで残りは確率比例。**確信のある点ほど払戻が厚い**。"""
    legs = [(1, 2, c) for c in range(3, 8)]
    odds = {c: 40.0 for c in legs}
    prob = {c: (10.0 if i == 0 else 1.0) for i, c in enumerate(legs)}
    st = allocate(legs, odds, prob, PLANS["C_hit"])
    assert st is not None
    pays = {c: st[c] * odds[c] for c in legs}
    assert pays[legs[0]] > pays[legs[-1]], "傾斜が付いていない"
    assert min(pays.values()) >= BUDGET * MIN_PAYOUT_MULT


# ───────────────── 🔴 床が置けないときに見送りにしない ─────────────────

def test_fallback_returns_the_previous_allocation():
    for key in HIT:
        fb = alloc_fallback(PLANS[key])
        assert fb is not None
        assert (fb.alloc, fb.floor_mult) == ALLOC_BEFORE_MIN_PAYOUT[key]
        assert fb.key == PLANS[key].key, "別名にすると1レース2商品になる"


def test_product_is_still_built_when_the_floor_does_not_fit():
    """🔴 Σ(1/予測オッズ) > 1/2.0 でも商品は作る（変更前の配分で売る）。

    `C_hit` は12点。全点 16.0倍なら床は ceil(20000/1600)=13単位 × 12 = 156単位で
    予算（100単位）に収まらない。ここで None を返すと母集団が静かに 8% 消える。
    """
    shape = RaceShape("C", 1.50, 0, 0.10, True, tuple(range(1, 8)), 0.0, ())
    po = {c: 16.0 for c in PERMS}          # C_hit の帯（15倍）は満たす
    pr = {c: 1.0 / len(PERMS) for c in PERMS}
    plan = PLANS["C_hit"]
    assert allocate([(1, 2, c) for c in range(3, 8)] + [(1, 3, c) for c in range(4, 8)]
                    + [(1, 4, c) for c in range(5, 8)], po, pr, plan) is None
    got = build_with_gate_fallback(shape, plan, po, pr)
    assert got is not None, "床が置けないだけで商品が消えている"
    legs, stakes, used = got
    assert sum(stakes.values()) == BUDGET
    assert used.key == "C_hit"


def test_fallback_is_not_used_when_the_floor_fits():
    """床が置けるレースでは変更前の配分へ落ちない（＝2倍保証が効いている）。"""
    shape = RaceShape("C", 1.50, 0, 0.10, True, tuple(range(1, 8)), 0.0, ())
    po = {c: 60.0 for c in PERMS}
    pr = {c: 1.0 / len(PERMS) for c in PERMS}
    got = build_with_gate_fallback(shape, PLANS["C_hit"], po, pr)
    assert got is not None
    legs, stakes, _ = got
    assert min_expected_payout(stakes, po) >= BUDGET * MIN_PAYOUT_MULT


def test_rule_version_covers_the_floor():
    """🔴 床を動かしたら版が割れること（新旧の行が混ざらないため）。"""
    import src.type_lab as M

    before7, before9 = M.rule_version(7), M.rule_version(9)
    orig = M.PLANS["C_hit"]
    try:
        M.PLANS["C_hit"] = replace(orig, floor_mult=2.5)
        assert M.rule_version(7) != before7
        assert M.rule_version(9) != before9
    finally:
        M.PLANS["C_hit"] = orig
    assert (M.rule_version(7), M.rule_version(9)) == (before7, before9)
