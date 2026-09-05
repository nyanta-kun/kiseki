"""ライン決着への差し替え（`line_legs` / `apply_line_swap`）を固定する（2026-09-05）。

発端はユーザー観察「他の並びの買い目は買っていて外している」。買い目と実際の
決着をライン構成で突き合わせると、**同一ライン3車がそのまま来る決着は19.3%
起きているのに、買い目の8.9%しか充てていなかった**（33,730レース）。

ここで固定するのは3点:

  1. 🔴 **入稿ゲートを割るなら差し替えない**（母集団を動かさない）。
     素で2点差し替えるとゲート通過が 88.8% → 58.0% に落ちる。
  2. 🔴 **落とすのは確率下位の末尾 m 点**。上位を触ると本線が崩れる。
  3. 🔴 **掛けるプランは4つだけ**。`A_hit`(3点) は実測で両窓とも悪化した。
"""
from __future__ import annotations

import inspect
import itertools

import pytest

from src.stake_allocation import MIN_MEAN_PAYOUT
from src.type_lab import (
    LINE_SWAP_LEGS, LINE_SWAP_MIN_KEEP, LINE_SWAP_PLANS, PLANS, RaceShape,
    _lines_of, allocate, apply_line_swap, build_with_gate_fallback, line_legs,
    mean_expected_payout, race_shape,
)

PERMS = list(itertools.permutations(range(1, 8), 3))
LINE = (1, 2, 3)
LINE_PERMS = list(itertools.permutations(LINE))


def _shape(lines=((1, 2, 3),)) -> RaceShape:
    return RaceShape("C", 1.50, 0, 0.10, True, tuple(range(1, 8)), 0.0, lines)


def _board(base: float, line_odds: dict) -> tuple[dict, dict]:
    po = {c: base for c in PERMS}
    po.update(line_odds)
    pr = {c: 1.0 / len(PERMS) for c in PERMS}
    return po, pr


# ───────────────────────── lines の取り出し ─────────────────────────

def test_lines_of_keeps_only_three_car_lines():
    """3車以上のラインだけ。単騎（0 / 空 / None）は含めない。"""
    assert _lines_of({1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 0, 7: None}) == ((1, 2, 3),)
    assert _lines_of({1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 0}) == ((3, 4, 5, 6),)
    assert _lines_of({c: 0 for c in range(1, 8)}) == ()


def test_race_shape_fills_lines():
    """`race_shape` が並びを `RaceShape.lines` へ載せること（載らないと差し替えが死ぬ）。"""
    cars = range(1, 8)
    shape = race_shape(
        {c: 0.9 - 0.1 * c for c in cars},
        {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 0, 7: 0},
        {c: 1 for c in cars}, {c: "逃" for c in cars},
        {c: 100.0 for c in cars}, {c: 10.0 for c in cars}, 1,
    )
    assert shape is not None
    assert shape.lines == ((1, 2, 3),)


# ───────────────────────── 掛けるプランの範囲 ─────────────────────────

def test_only_four_plans_are_targeted():
    """🔴 `A_hit` は実測で両窓とも悪化した（-3.26 / -1.74pt）ので入れない。"""
    assert LINE_SWAP_PLANS == {"B_hit", "C_hit", "E_hit", "F_hit"}
    assert "A_hit" not in LINE_SWAP_PLANS


@pytest.mark.parametrize("key", ["A_hit", "A_trio", "A_ana", "D_hit", "F_pay"])
def test_other_plans_are_untouched(key):
    po, pr = _board(60.0, {p: 4.0 for p in LINE_PERMS})
    legs = [c for c in PERMS if c not in LINE_PERMS][:6]
    assert line_legs(_shape(), PLANS[key], legs, po, 2) is None


def test_trio_plans_are_untouched():
    """三連複は順序リスクが無いので対象外（`bet_type` で弾く）。"""
    assert PLANS["D_hit"].bet_type == "trio"
    assert PLANS["A_trio"].bet_type == "trio"


# ───────────────────────── 差し替えの形 ─────────────────────────

def test_swap_keeps_the_point_count_and_drops_the_tail():
    """点数は変えない。落とすのは末尾 m 点だけで、先頭は1点も動かない。"""
    po, pr = _board(60.0, {p: 4.0 + i for i, p in enumerate(LINE_PERMS)})
    legs = [c for c in PERMS if c not in LINE_PERMS][:6]
    out = line_legs(_shape(), PLANS["C_hit"], legs, po, 2)
    assert out is not None
    assert len(out) == len(legs)
    assert out[:4] == legs[:4]              # 先頭は保存
    assert legs[4] not in out and legs[5] not in out


def test_swap_picks_the_most_popular_line_combos():
    """差し替え先は**予測オッズ昇順（人気順）**。確率順ではない。"""
    odds = {p: 90.0 for p in LINE_PERMS}
    odds[(2, 1, 3)] = 5.0
    odds[(1, 2, 3)] = 7.0
    po, pr = _board(60.0, odds)
    legs = [c for c in PERMS if c not in LINE_PERMS][:6]
    out = line_legs(_shape(), PLANS["C_hit"], legs, po, 2)
    assert out[-2:] == [(2, 1, 3), (1, 2, 3)]


def test_line_must_be_fully_present_in_the_buy():
    """3車のうち1車でも買い目に出ていないラインは対象外。"""
    po, pr = _board(60.0, {p: 4.0 for p in LINE_PERMS})
    legs = [c for c in PERMS if 3 not in c and c not in LINE_PERMS][:6]
    assert line_legs(_shape(), PLANS["C_hit"], legs, po, 2) is None


def test_never_shrinks_below_the_minimum_kept():
    """差し替え後に `LINE_SWAP_MIN_KEEP` 点を割るなら差し替えない。"""
    po, pr = _board(60.0, {p: 4.0 for p in LINE_PERMS})
    legs = [(1, 2, 4), (1, 3, 5), (2, 3, 6), (1, 2, 7)]   # 1・2・3 が揃っている
    assert line_legs(_shape(), PLANS["C_hit"], legs, po, 1) is not None   # 4-1=3 点
    assert line_legs(_shape(), PLANS["C_hit"], legs, po, 2) is None       # 4-2=2 点
    assert LINE_SWAP_MIN_KEEP == 3


def test_no_line_no_swap():
    po, pr = _board(60.0, {p: 4.0 for p in LINE_PERMS})
    legs = [c for c in PERMS if c not in LINE_PERMS][:6]
    assert line_legs(_shape(lines=()), PLANS["C_hit"], legs, po, 2) is None


# ───────────────────── 🔴 ゲートを割るなら差し替えない ─────────────────────

def _legs_and_stakes(po, pr, plan, n=6):
    legs = [c for c in PERMS if c not in LINE_PERMS][:n]
    stakes = allocate(legs, po, pr, plan)
    assert stakes is not None
    return legs, stakes


def test_swap_is_refused_when_it_would_break_the_gate():
    """🔴 これが壊れると入稿ゲートの通過が 88.8% → 58.0% に落ちる。"""
    po, pr = _board(60.0, {p: 2.0 for p in LINE_PERMS})   # 人気すぎる並び
    plan = PLANS["C_hit"]
    legs, stakes = _legs_and_stakes(po, pr, plan)
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT
    out_legs, out_stakes = apply_line_swap(_shape(), plan, legs, stakes, po, pr)
    assert out_legs == legs
    assert out_stakes is stakes


def test_swap_falls_back_from_two_legs_to_one():
    """2点で割るなら1点で試す（`LINE_SWAP_LEGS` の順序）。"""
    assert LINE_SWAP_LEGS == (2, 1)
    odds = {p: 90.0 for p in LINE_PERMS}
    odds[(2, 1, 3)] = 4.0
    odds[(1, 2, 3)] = 4.5
    po, pr = _board(60.0, odds)
    plan = PLANS["C_hit"]
    legs, stakes = _legs_and_stakes(po, pr, plan)
    assert line_legs(_shape(), plan, legs, po, 2) is not None      # 2点は組める
    out_legs, out_stakes = apply_line_swap(_shape(), plan, legs, stakes, po, pr)
    assert len(out_legs) == len(legs)
    assert out_legs[-1] == (2, 1, 3)          # 1点だけ差し替わった
    assert out_legs[-2] == legs[-2]
    assert mean_expected_payout(out_stakes, po) > MIN_MEAN_PAYOUT


def test_swap_fires_when_the_gate_still_passes():
    odds = {p: 90.0 for p in LINE_PERMS}
    odds[(2, 1, 3)] = 30.0
    odds[(1, 2, 3)] = 35.0
    po, pr = _board(60.0, odds)
    plan = PLANS["C_hit"]
    legs, stakes = _legs_and_stakes(po, pr, plan)
    out_legs, out_stakes = apply_line_swap(_shape(), plan, legs, stakes, po, pr)
    assert out_legs[-2:] == [(2, 1, 3), (1, 2, 3)]
    assert len(out_legs) == len(legs)
    assert mean_expected_payout(out_stakes, po) > MIN_MEAN_PAYOUT


# ───────────────────────── 配線 ─────────────────────────

def test_rule_version_covers_the_swap():
    """🔴 対象プランや点数を動かしたら版が割れること（新旧の行が混ざらないため）。"""
    import src.type_lab as M

    before7, before9 = M.rule_version(7), M.rule_version(9)
    orig = M.LINE_SWAP_PLANS
    try:
        M.LINE_SWAP_PLANS = frozenset({"C_hit"})
        assert M.rule_version(7) != before7
        assert M.rule_version(9) != before9      # 9車にも掛けているので割れる
    finally:
        M.LINE_SWAP_PLANS = orig
    assert (M.rule_version(7), M.rule_version(9)) == (before7, before9)


def test_build_with_gate_fallback_routes_through_the_swap():
    """🔴 生成の唯一の入口が差し替えを通ること（paper と live を割らないため）。"""
    src = inspect.getsource(build_with_gate_fallback)
    assert "apply_line_swap" in src
    # 早期 return（9車）も含め、素の `(*got, plan)` が残っていないこと
    assert "return (*got, plan)" not in src
    assert "return (*alt, fb)" not in src
