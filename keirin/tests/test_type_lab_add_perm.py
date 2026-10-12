"""並び違いの押さえ（`apply_add_perm`）を固定する（2026-09-21）。

発端はユーザー観察「三連単で多点買う際に1点目の並び違いで確定し、その目を買って
いないことが多い。多点広げる前に、絶対に逆転がないというケースを除いて、1点目の
並び違いは買った方が良いのでは？」（09-21 広島2R `E_hit` 決着 4-3-1 /
大宮2R `A_ana` 決着 7-2-1）。

検証（`scripts/exp_type_lab/order_depth.py`）で分かれたのは次の3点で、ここでは
それを機械的に固定する:

  1. 🔴 **「減らして入れる」ではなく「足す」**。点数据え置きで深さを優先すると
     集合カバレッジを 7.3pt 失って差し引きマイナス（全体 −0.65/−0.47 〜 −1.42/−1.28）。
  2. 🔴 **足す1点は予測オッズ最安**（確率最上位ではない）。実測で全体
     +0.59/+0.64 → **+0.66/+0.71**。発端2件とも決着はモデル確率で最下位側なのに
     市場では集合内最安だった。
  3. 🔴 **掛けるのは `E_hit` だけ**（2026-10-12・#613）。`C_hit` は対照に負け、
     `A_ana` は ΔROI −11.9pt（探索窓で有意）。`A_hit` / `B_hit` は計画払戻5万円の
     ダッチへ替わった後、足すと目標額が半減するので外した。

足す1点の選び方・帯・ゲートの検査（機構の検査）は、ダッチの商品にも掛け直せるよう
`B_hit` を一時的に対象へ入れて走らせる（`_with_b_hit`）。本番の対象は別の検査で固定する。
"""
from __future__ import annotations

import itertools

import pytest

import src.type_lab as TL
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS
from src.type_lab import (
    ADD_PERM_PLANS, PLANS, RaceShape, allocate, apply_add_perm,
    build_with_gate_fallback, mean_expected_payout, rule_version,
)

PERMS = list(itertools.permutations(range(1, 8), 3))


def _shape(type_label: str = "B") -> RaceShape:
    return RaceShape(type_label, 1.50, 0, 0.10, True, tuple(range(1, 8)), 0.0,
                     ((1, 2, 3), (4, 5), (6, 7)))


def _board(base: float = 60.0, over: dict | None = None):
    po = {c: base for c in PERMS}
    if over:
        po.update(over)
    pr = {c: 1.0 / len(PERMS) for c in PERMS}
    return po, pr


# ───────────────────────── 対象プラン ─────────────────────────

def test_plans_are_exactly_e_hit():
    """🔴 対象は `E_hit` だけ。

    - `C_hit` は Δ +0.10 で無作為対照 +0.32 に負け、ΔROI −0.95 は有意。
    - `A_ana` は表示的中 +0.88 だが **ΔROI −11.9pt（探索窓で有意）・払戻中央 −8.3%**
      ＝軸1を外す穴狙い商品に安い並びを足すと払戻が崩れる。発端の大宮2R はこのプランなので、
      **あのレースを救う操作は商品全体では損**という結論そのもの。
    - 🔴 `A_hit` / `B_hit` は 2026-10-12 に外した（#613）。計画払戻5万円のダッチでは
      計画払戻 = 予算 ÷ Σ(1/予測オッズ) なので、安い並びを1点足すと目標額が半減する。
      外すと 4万+/日 +0.29（両窓）・表示的中 −1.25/−1.33pt。
    """
    assert ADD_PERM_PLANS == frozenset({"E_hit"})
    for key in ("A_hit", "B_hit", "C_hit", "A_ana", "A_trio", "D_hit", "F_sign", "F_pay"):
        assert key not in ADD_PERM_PLANS


@pytest.mark.parametrize("key", ["A_hit", "B_hit"])
def test_no_op_for_dutch_hit_plans(key):
    """🔴 `A_hit` / `B_hit` には足さない（足すと計画払戻が半減する）。"""
    plan = PLANS[key]
    assert plan.alloc == "dutch"
    po, pr = _board(60.0, {(3, 2, 1): 25.0})
    legs = [(1, 2, 3), (4, 5, 6)]
    st = allocate(legs, po, pr, plan)
    out_legs, out_st = apply_add_perm(plan, legs, st, po, pr)
    assert [tuple(c) for c in out_legs] == legs
    assert out_st == st


@pytest.fixture
def _with_b_hit(monkeypatch):
    """機構の検査用に `B_hit` を一時的に対象へ入れる（本番の対象は上の検査で固定）。"""
    monkeypatch.setattr(TL, "ADD_PERM_PLANS", TL.ADD_PERM_PLANS | {"B_hit"})


def test_no_op_for_plans_outside_the_set():
    po, pr = _board()
    legs = [(1, 2, 3), (1, 3, 2)]
    st = allocate(legs, po, pr, PLANS["C_hit"])
    out_legs, out_st = apply_add_perm(PLANS["C_hit"], legs, st, po, pr)
    assert [tuple(c) for c in out_legs] == legs
    assert out_st == st


def test_no_op_for_trio_plans():
    """三連複は順序が無いので定義上掛からない。"""
    plan = PLANS["A_trio"]
    t3 = {frozenset(c): 8.0 for c in itertools.combinations(range(1, 8), 3)}
    pr3 = {k: 1.0 / len(t3) for k in t3}
    legs = [frozenset({1, 2, 3}), frozenset({1, 2, 4})]
    st = allocate(legs, t3, pr3, plan)
    out_legs, out_st = apply_add_perm(plan, legs, st, t3, pr3)
    assert list(out_legs) == legs
    assert out_st == st


# ───────────────────────── 足す1点の選び方 ─────────────────────────

def test_adds_the_cheapest_unbought_permutation_of_the_first_leg(_with_b_hit):
    """🔴 **先頭の目と同じ3車**のうち、**予測オッズ最安**を1点足す。

    確率を最安の目だけ極端に低くしても選ばれること（＝確率で選んでいないこと）を
    固定する。ここが確率最上位に戻ると、発端の大宮2R が拾えなくなる
    （{1,2,7} の確率最上位は `2-1-7`=6位で、決着 `7-2-1` は7位＝最安だった）。
    """
    plan = PLANS["B_hit"]
    po, pr = _board(60.0, {(3, 2, 1): 25.0, (2, 1, 3): 30.0})
    pr[(3, 2, 1)] = 1e-9                      # 最安だが確率は最下位
    legs = [(1, 2, 3), (4, 5, 6)]
    st = allocate(legs, po, pr, plan)
    out_legs, out_st = apply_add_perm(plan, legs, st, po, pr)
    assert len(out_legs) == 3
    assert (3, 2, 1) in {tuple(c) for c in out_legs}
    assert sum(out_st.values()) == sum(st.values())      # 投資は据え置き


def test_uses_the_first_leg_not_another_bought_set(_with_b_hit):
    """足す先は**先頭の目の集合**。他の集合の並び違いは足さない（1点だけ）。"""
    plan = PLANS["B_hit"]
    po, pr = _board(60.0, {(6, 5, 4): 5.0})   # 2点目の集合の方が安い並びを持つ
    legs = [(1, 2, 3), (4, 5, 6)]
    st = allocate(legs, po, pr, plan)
    out_legs, _ = apply_add_perm(plan, legs, st, po, pr)
    added = {tuple(c) for c in out_legs} - {tuple(c) for c in legs}
    assert len(added) == 1
    assert {frozenset(c) for c in added} == {frozenset({1, 2, 3})}


def test_never_adds_an_already_bought_permutation(_with_b_hit):
    plan = PLANS["B_hit"]
    po, pr = _board(60.0)
    legs = [tuple(c) for c in itertools.permutations((1, 2, 3))]   # 6順列すべて
    st = allocate(legs, po, pr, plan)
    out_legs, out_st = apply_add_perm(plan, legs, st, po, pr)
    assert [tuple(c) for c in out_legs] == legs                    # 足せない
    assert out_st == st


# ───────────────────────── 帯とゲート ─────────────────────────

def test_keeps_the_band():
    """🔴 帯（`min_odds`）の外へは移さない。`E_hit` は 30倍以上。

    帯を無視しても実測の効果はほぼ増えない（確認窓 +0.64 → +0.67）ので、
    商品の価格帯に触る理由が無い。
    """
    plan = PLANS["E_hit"]
    assert plan.min_odds == 30.0
    po, pr = _board(60.0, {(3, 2, 1): 12.0, (2, 1, 3): 45.0})
    legs = [(1, 2, 3), (4, 5, 6)]
    st = allocate(legs, po, pr, plan)
    out_legs, _ = apply_add_perm(plan, legs, st, po, pr)
    added = {tuple(c) for c in out_legs} - {tuple(c) for c in legs}
    assert added == {(2, 1, 3)}          # 12倍の方が安いが帯の外なので採らない


def test_does_not_add_when_it_would_break_the_mean_payout_gate(_with_b_hit):
    """🔴 平均想定払戻が `MIN_MEAN_PAYOUT` を割るなら足さない（在庫を減らさない）。"""
    plan = PLANS["B_hit"]
    po, pr = _board(2.5, {(3, 2, 1): 2.1})      # 全点が安く、足すと平均が落ちる
    legs = [(1, 2, 3), (1, 3, 2)]
    st = allocate(legs, po, pr, plan)
    before = mean_expected_payout(st, po)
    out_legs, out_st = apply_add_perm(plan, legs, st, po, pr)
    if before <= MIN_MEAN_PAYOUT:
        return                                   # 前提が崩れる盤面なら検査しない
    assert mean_expected_payout(out_st, po) > MIN_MEAN_PAYOUT
    if [tuple(c) for c in out_legs] != legs:
        assert min(float(po[c]) for c in out_st) >= MIN_POINT_ODDS


def test_never_lets_a_point_fall_below_min_point_odds(_with_b_hit):
    """🔴 全点の予測オッズ >= `MIN_POINT_ODDS`。1点でも割るなら足さない。"""
    plan = PLANS["B_hit"]
    po, pr = _board(60.0, {(3, 2, 1): 1.5})     # 2.0倍未満は候補にならない
    legs = [(1, 2, 3), (4, 5, 6)]
    st = allocate(legs, po, pr, plan)
    out_legs, out_st = apply_add_perm(plan, legs, st, po, pr)
    assert (3, 2, 1) not in {tuple(c) for c in out_legs}
    assert min(float(po[c]) for c in out_st) >= MIN_POINT_ODDS


# ───────────────────────── 本番経路と車数 ─────────────────────────

def test_wired_into_build_with_gate_fallback_for_7_cars():
    """🔴 `build_with_gate_fallback` を通して効くこと（生成側だけに書かない）。"""
    plan = PLANS["E_hit"]
    po, pr = _board(60.0)
    pr[(1, 2, 3)] = 0.5                          # 先頭の目を確定させる
    got = build_with_gate_fallback(_shape("E"), plan, po, pr, 7)
    assert got
    legs = [tuple(c) for c in got[0]]
    first_set = frozenset(legs[0])
    assert sum(1 for c in legs if frozenset(c) == first_set) >= 2


def test_not_wired_for_b_hit_in_production():
    """🔴 本番経路でも `B_hit` には足さない（#613）。"""
    plan = PLANS["B_hit"]
    po, pr = _board(60.0)
    pr[(1, 2, 3)] = 0.5
    got = build_with_gate_fallback(_shape("B"), plan, po, pr, 7)
    assert got
    legs = [tuple(c) for c in got[0]]
    base = TL.build_legs(_shape("B"), plan, po, pr)
    assert len(legs) == len(base)            # 足されていない


def test_not_applied_for_9_cars():
    """🔴 9車には掛けない（測ったのは7車）。`rule_version(9)` も動かさないこと。"""
    import src.type_lab as TL
    perms9 = list(itertools.permutations(range(1, 10), 3))
    po = {c: 60.0 for c in perms9}
    pr = {c: 1.0 / len(perms9) for c in perms9}
    pr[(1, 2, 3)] = 0.5
    shape = RaceShape("B", 1.50, 0, 0.10, True, tuple(range(1, 10)), 0.0,
                      ((1, 2, 3), (4, 5, 6), (7, 8, 9)))
    got = build_with_gate_fallback(shape, PLANS["B_hit"], po, pr, 9)
    if got:
        legs = [tuple(c) for c in got[0]]
        base = TL.build_legs(shape, PLANS["B_hit"], po, pr)
        assert len(legs) == len(base)            # 足されていない


def test_rule_version_tracks_the_plan_set_for_7_cars_only():
    """🔴 対象プランを変えたら 7車の版が割れる。9車の版は動かさない。"""
    import src.type_lab as TL
    v7, v9 = rule_version(7), rule_version(9)
    orig = TL.ADD_PERM_PLANS
    try:
        TL.ADD_PERM_PLANS = frozenset({"A_hit", "E_hit"})
        assert rule_version(7) != v7
        assert rule_version(9) == v9
    finally:
        TL.ADD_PERM_PLANS = orig
    assert rule_version(7) == v7
