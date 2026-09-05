"""9車型F の三連複 `F_line`（2026-09-06）。

実測と設計の根拠は `docs/type_lab/nine_car_type_f_2026_09_06.md`。
ここで固定するのは **doc の結論が壊れたら落ちる**ところだけ:

  ① 軸は「3着内率の合計が最大のライン」の2車（指数上位2車ではない）
  ② 点数は固定せず、計画払戻が `MIN_MEAN_PAYOUT` を割らない限界まで積む
  ③ 配分はダッチ（conf傾斜ではない）
  ④ 3点も積めないレースは**商品にしない**
  ⑤ 売るのは 9車の決勝以外だけ（7車と 9車決勝は据え置き）
"""
from __future__ import annotations

import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.stake_allocation import MIN_MEAN_PAYOUT          # noqa: E402
from src.type_lab import (                                # noqa: E402
    BUDGET, PLANS, TRIO_LINE_MIN_LEGS, TRIO_LINE_SIGMA_MAX,
    RaceShape, _strongest_pair, allocate, build_legs, mean_expected_payout,
    race_shape, sell_plans_for,
)

PLAN = PLANS["F_line"]


# ───────────────────────── ① 軸の取り方 ─────────────────────────

def test_strongest_pair_picks_the_line_with_the_best_pair_not_the_best_car():
    """🔴 **単独最強の車がいるラインが選ばれるとは限らない**（2車の合計で決める）。

    doc §3: 軸2をライン内から取るのが両窓で最良（全体の3着内率上位から取ると
    確認窓 31.0% まで落ちる）。ここが「合計」でなく「1位の車」になると別物になる。
    """
    lines = ((1, 2, 3), (4, 5))
    p3 = {1: .50, 2: .10, 3: .05, 4: .45, 5: .44, 6: .20}
    # 1番が単独最強でも、ペアの合計は 4-5（0.89）が 1-2（0.60）を上回る
    assert _strongest_pair(lines, p3) == (4, 5)


def test_strongest_pair_orders_by_top3_rate_not_formation():
    """🔴 三連複なので順序は要らない。**3着内率の降順**で返す（隊列順ではない）。"""
    # 隊列は (3, 1, 2) だが 3着内率は 1 > 3
    assert _strongest_pair(((3, 1, 2),), {1: .5, 2: .1, 3: .3}) == (1, 3)


def test_strongest_pair_is_empty_without_a_two_car_line():
    assert _strongest_pair((), {1: .5, 2: .4}) == ()


def test_race_shape_fills_line_pair():
    """`race_shape` が `line_pair` を載せること（載らないと `F_line` が組めない）。"""
    cars = range(1, 10)
    # 4-5 ラインを最強にする（3着内率の合計で 1-2-3 ラインを上回る）
    p3 = {1: .30, 2: .05, 3: .04, 4: .29, 5: .28, 6: .10,
          7: .09, 8: .08, 9: .07}
    shape = race_shape(
        p3,
        {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 0, 7: 0, 8: 0, 9: 0},
        {1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 0, 7: 0, 8: 0, 9: 0},
        {c: "逃" for c in cars}, {c: 100.0 for c in cars},
        {c: 10.0 for c in cars}, 1,
    )
    assert shape is not None
    assert shape.line_pair == (4, 5)


# ───────────────────────── 買い目 ─────────────────────────

def _shape(pair=(4, 5), order=(1, 4, 5, 2, 3, 6, 7, 8, 9)):
    return RaceShape("F", 1.20, 0, 0.10, False, tuple(order), 0.0,
                     ((1, 2, 3), (4, 5)), tuple(pair))


def _odds(value=30.0):
    """全三連複に同じ予測オッズを置く。"""
    return {frozenset(c): value for c in itertools.combinations(range(1, 10), 3)}


def test_every_leg_contains_both_axes_and_partners_follow_the_index_order():
    """軸2車は全点に入り、相手は `shape.order`（3着内率の降順）の順で積む。"""
    shape = _shape()
    legs = build_legs(shape, PLAN, _odds(30.0), {})
    assert legs is not None
    assert all({4, 5} <= set(c) for c in legs)
    partners = [next(iter(set(c) - {4, 5})) for c in legs]
    # order から軸2車を除いた並び＝ 1, 2, 3, 6, 7, 8, 9
    assert partners == [1, 2, 3, 6, 7, 8, 9]


def test_point_count_is_not_fixed_but_follows_the_planned_payout():
    """🔴 **点数は固定しない。** 予測オッズが高いほど多く積める（doc §5）。

    ダッチでは計画払戻 = 予算 ÷ Σ(1/オッズ) なので、
    「Σ が `TRIO_LINE_SIGMA_MAX` を超えない最大点数」＝
    「計画払戻が `MIN_MEAN_PAYOUT` を割らない最大点数」。
    """
    cheap = build_legs(_shape(), PLAN, _odds(12.0), {})     # 1/12 * 6 = 0.50
    rich = build_legs(_shape(), PLAN, _odds(60.0), {})      # 1/60 * 7 = 0.117
    assert cheap is not None and rich is not None
    assert len(cheap) < len(rich)
    for legs, o in ((cheap, 12.0), (rich, 60.0)):
        assert sum(1.0 / o for _ in legs) <= TRIO_LINE_SIGMA_MAX


def test_sigma_max_is_tied_to_the_submission_gate():
    """🔴 Σ の上限は入稿ゲートから導く。片方だけ動かすと商品が静かに別物になる。"""
    assert TRIO_LINE_SIGMA_MAX == pytest.approx(BUDGET / MIN_MEAN_PAYOUT)
    assert PLAN.sigma_max == pytest.approx(TRIO_LINE_SIGMA_MAX)


def test_stops_at_the_first_point_that_would_break_the_floor():
    """🔴 **超えたら止める（飛ばして先を拾わない）。**

    相手は3着内率の降順なので、ここで `continue` にすると
    「安い相手を外して穴だけ買う」別の商品になる（doc §5 の実測は前方から積む形）。
    """
    od = _odds(60.0)
    # 2番目の相手（=2番）だけ極端に安くして、そこで止まることを見る。
    # Σ は 1/60(=0.017) + 1/1.9(=0.526) で上限 0.5 を超える。
    od[frozenset({4, 5, 2})] = 1.9
    legs = build_legs(_shape(), PLAN, od, {})
    assert legs is None or [next(iter(set(c) - {4, 5})) for c in legs] == [1]
    # 🔴 3番以降を拾っていないこと。拾っていたら `continue` に戻っている＝
    #    「安い相手を外して穴だけ買う」別の商品になっている。
    if legs is not None:
        assert not ({3, 6, 7, 8, 9} & {next(iter(set(c) - {4, 5})) for c in legs})


def test_no_product_when_fewer_than_the_minimum_points_fit():
    """🔴 3点も積めないレースは**商品にしない**（1万円以下は当たりと扱わないため）。"""
    assert build_legs(_shape(), PLAN, _odds(5.0), {}) is None      # 1/5*3 = 0.6 > 0.5
    assert TRIO_LINE_MIN_LEGS == 3


def test_no_product_without_a_two_car_line():
    """単騎ばかりで2車以上のラインが無ければ組まない（軸が定義できない）。"""
    shape = RaceShape("F", 1.20, 0, 0.10, False, tuple(range(1, 10)), 0.0, (), ())
    assert build_legs(shape, PLAN, _odds(30.0), {}) is None


# ───────────────────────── ③ 配分 ─────────────────────────

def test_allocation_is_dutch_so_every_point_pays_the_same():
    """🔴 conf傾斜ではなくダッチ（doc §6・conf床2倍は両指標で負ける）。

    ダッチなら**どの点が当たっても払戻が同じ**なので、ガミが構造的に起きない。
    """
    assert PLAN.alloc == "dutch"
    shape = _shape()
    od = {frozenset({4, 5, 1}): 20.0, frozenset({4, 5, 2}): 40.0,
          frozenset({4, 5, 3}): 60.0, frozenset({4, 5, 6}): 80.0}
    od.update({k: v for k, v in _odds(500.0).items() if k not in od})
    legs = build_legs(shape, PLAN, od, {})
    stakes = allocate(legs, od, {}, PLAN)
    assert stakes is not None
    pays = sorted(stakes[c] * od[c] for c in stakes)
    # 100円単位の丸めぶんだけずれる
    assert pays[-1] - pays[0] < 0.25 * pays[0]
    assert pays[0] > BUDGET, "当たったら投資を超えること"


def test_the_built_product_passes_the_submission_gate():
    """組めた商品は入稿ゲート（平均想定払戻 > 2万円）を通ること。"""
    shape = _shape()
    od = _odds(30.0)
    legs = build_legs(shape, PLAN, od, {})
    stakes = allocate(legs, od, {}, PLAN)
    assert stakes is not None
    assert mean_expected_payout(stakes, od) > MIN_MEAN_PAYOUT


# ───────────────────────── ⑤ 売り分け ─────────────────────────

def test_only_nine_car_non_final_sells_the_trio():
    """🔴 7車と 9車の決勝は**据え置き**（7車では測っていない）。"""
    for rt in ("準決勝", "選抜", "特選", "一予選", "一般", "", None):
        assert [p.key for p in sell_plans_for("F", 9, rt)] == ["F_line"], rt
    assert [p.key for p in sell_plans_for("F", 9, "決勝")] == ["F_pay"]
    assert [p.key for p in sell_plans_for("F", 9, "チャレンジ決勝")] == ["F_pay"]
    for rt in ("選抜", "一般", "特選", "一予選"):
        assert [p.key for p in sell_plans_for("F", 7, rt)] == ["F_hit"], rt


def test_trio_plan_is_generated_for_every_car_count():
    """🔴 生成は 7車でも行う（比較台を残す・`plans_for` の思想）。"""
    from src.type_lab import plans_for
    for n in (7, 9):
        assert "F_line" in [p.key for p in plans_for("F", n, "選抜")]


# ───────────────────────── 入稿文面（軸の差し替え） ─────────────────────────

def test_submission_uses_the_line_axes_not_the_index_top_two():
    """🔴 **`F_line` の印と文面は買い目から軸を拾い直すこと。**

    `type_lab_picks.axis1/axis2` は指数（3着内率）の上位2車で、`F_line` の軸
    （最強ラインの2車）と一致するのは 9車型F の 44.5% しかない。そのまま使うと
    **◎が「1点にしか出てこない車」に付き、毎点に入っている本当の軸が △ になる**。
    例外もログも出ないまま、印と買い目が食い違う。
    """
    from src.type_lab_submission import axes_from_legs, build_submission

    # 指数順は 1-7-5-3-9-… だが、買っているのは 1 と 9 を軸にした7点
    legs = [{"combo": f"1={c}=9", "stake": 1000, "pred_odds": 20.0}
            for c in (6, 3, 7, 5, 2, 8, 4)]
    row = {"plan_key": "F_line", "type_label": "F",
           "axis1": 1, "axis2": 7,            # ← 指数上位2車（7 は軸ではない）
           "p3_order": "1-7-5-3-9-2-6-8-4", "bet_type": "trio", "legs": legs}
    assert axes_from_legs(legs, row["p3_order"]) == (1, 9)

    sub = build_submission(row)
    assert sub["marks"][1] == "◎"
    assert sub["marks"][9] == "○", "毎点に入っている車が ○ になること"
    assert sub["marks"][7] != "○", "指数2位でも軸でなければ ○ にしない"
    assert "◎1番・○9番" in sub["comment"]
    # 🔴 指数上位2車だと誤解させる文言を入れない
    assert "指数の上位2車ではなく" in sub["comment"]


def test_axes_from_legs_returns_nothing_without_a_common_car():
    """共通の車が無ければ空（差し替えず従来どおりに落ちる）。"""
    from src.type_lab_submission import axes_from_legs
    assert axes_from_legs([{"combo": "1=2=3"}, {"combo": "4=5=6"}], "1-2-3-4-5-6-7") == ()


def test_axes_from_legs_ignores_the_upper_band():
    """🔴 上帯（押さえ）を混ぜると共通車が消える。`_base_legs` で除くこと。"""
    from src.type_lab_submission import axes_from_legs
    legs = [{"combo": "1=4=5"}, {"combo": "1=4=2"},
            {"combo": "6=7=3", "role": "band"}]
    assert axes_from_legs(legs, "1-4-5-2-3-7-6") == (1, 4)
