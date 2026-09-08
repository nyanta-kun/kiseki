"""型C の「帯の下から最人気の1点を買い足す」を固定する（2026-09-08）。

発端は 2026-09-08 大垣7R。決着 7-3-5 は p3 上位3車の順当決着かつ**市場最人気**
（6.0倍）だったのに、`min_odds=15.0` の帯に切られて買い目に無かった。
C_hit が外したレースの 29.7 / 26.9%（探索 / 確認）がこの形。

固定するのは次の4点。どれが壊れても**エラーは出ず、静かに別の商品になる**:

1. 差し込むのは **1点だけ**（2点にすると +6.47/+5.28pt → +3.81/+2.93pt へ落ちる）
2. 選ぶのは **最も人気（予測オッズ最小）の目**であって、モデル確率1位ではない
   （対照実験でモデル1位は無作為と区別できなかった。`_insert_underband` の docstring）
3. **点数は12点のまま**（確率最下位の1点と入れ替える）
4. **先頭へ入れる**（`line_legs` が後ろから置き換えるので、末尾だと真っ先に消える）

実測と再現: `docs/type_lab/type_c.md` 11章 /
`scripts/exp_type_lab/type_c_lowband.py`（diag / arms / ctrl / band / final / verify）
"""
from __future__ import annotations

import itertools

from src.stake_allocation import MIN_MEAN_PAYOUT
from src.type_lab import (
    GATE_FALLBACK, PLANS, RaceShape, build_legs, build_with_gate_fallback,
    mean_expected_payout, rule_version,
)

PERMS = list(itertools.permutations(range(1, 8), 3))
#: 大垣7R の p3 順（7-3-5-6-2-1-4）。`build_legs` は order しか見ない。
ORDER = (7, 3, 5, 6, 2, 1, 4)


def _shape() -> RaceShape:
    return RaceShape("C", 1.6728, 2, 0.20, False, ORDER, 1.5)


def _board(cheap: dict[tuple, float]) -> tuple[dict, dict]:
    """`cheap` の目だけ安く、残りは 40倍の板。確率は 1/オッズ に比例。"""
    po = {c: 40.0 for c in PERMS} | cheap
    tot = sum(1.0 / v for v in po.values())
    return po, {c: (1.0 / v) / tot for c, v in po.items()}


# ───────────────────────── 差し込みそのもの ─────────────────────────

def test_the_cheapest_under_band_point_is_bought():
    """帯15倍未満の最人気の目が買い目に入る（＝大垣7R の 7-3-5 を拾う）。"""
    po, pr = _board({(7, 3, 5): 6.2, (7, 5, 3): 6.9, (3, 7, 5): 14.0})
    legs = build_legs(_shape(), PLANS["C_hit"], po, pr)
    assert legs is not None
    assert legs[0] == (7, 3, 5), "帯下の最人気を先頭に入れていない"
    assert len(legs) == 12, "点数が12点から動いている"


def test_only_one_under_band_point_is_inserted():
    """🔴 差し込むのは1点だけ。2点目は +2.9〜3.8pt まで効果が落ちる。"""
    po, pr = _board({(7, 3, 5): 6.2, (7, 5, 3): 6.9, (3, 7, 5): 14.0})
    legs = build_legs(_shape(), PLANS["C_hit"], po, pr)
    under = [c for c in legs if po[c] < PLANS["C_hit"].min_odds]
    assert under == [(7, 3, 5)]


def test_the_pick_is_by_market_price_not_by_model_probability():
    """🔴 選ぶのは**最も人気の目**。モデル確率1位を選ぶ実装にしてはいけない
    （対照でモデル1位は無作為と区別できず、市場1位が最良だった）。"""
    po = {c: 40.0 for c in PERMS} | {(7, 3, 5): 6.2, (3, 7, 5): 9.0}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / v) / tot for c, v in po.items()}
    pr[(3, 7, 5)] = 1.0                       # 確率だけ 3-7-5 を最上位にする
    legs = build_legs(_shape(), PLANS["C_hit"], po, pr)
    assert legs[0] == (7, 3, 5), "確率で選んでいる（市場最人気で選ぶこと）"


def test_no_insert_when_nothing_sits_under_the_band():
    """帯下に目が無ければ何もしない（現行と同じ12点）。"""
    po, pr = _board({})
    legs = build_legs(_shape(), PLANS["C_hit"], po, pr)
    assert all(po[c] >= PLANS["C_hit"].min_odds for c in legs)
    assert len(legs) == 12


def test_points_below_the_floor_are_not_bought():
    """`underband_min` 未満（＝人気すぎる目）は拾わない。"""
    po, pr = _board({(7, 3, 5): 2.5})         # 5.0倍未満
    legs = build_legs(_shape(), PLANS["C_hit"], po, pr)
    assert (7, 3, 5) not in legs


def test_other_plans_are_untouched():
    """🔴 差込は型Cだけ。他の `prob_top` プランに漏れていないこと。"""
    assert PLANS["C_hit"].underband_min == 5.0
    for key, plan in PLANS.items():
        if key != "C_hit":
            assert plan.underband_min == 0.0, f"{key} に差込が漏れている"


# ───────────────────────── ゲートとの噛み合わせ ─────────────────────────

def test_falls_back_to_the_plain_twelve_when_the_gate_would_reject():
    """🔴 差込で平均想定払戻が2万円を割ったら、差込なしの12点で売る。

    これが無いと在庫が 8.86 → 7.30件/日 へ減る（＝母集団を静かに削る）。
    """
    shape = _shape()
    # 残りが 28倍の板。差し込むと安い点の床（予算×2.0÷6.2 ≈ 3,300円）が予算の
    # 3割を食い、平均想定払戻が2万円を割る。差込なしの12点なら 23,333円で通る。
    po = {c: 28.0 for c in PERMS} | {(7, 3, 5): 6.2}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / v) / tot for c, v in po.items()}

    # 前提: 差込のままではゲートに落ちること
    only = build_with_gate_fallback(shape, PLANS["C_hit"], po, pr, n_entries=7,
                                    min_mean_payout=10 ** 12)
    assert only is not None and (7, 3, 5) in only[1]
    assert mean_expected_payout(only[1], po) <= MIN_MEAN_PAYOUT, (
        "前提が崩れている: この盤面では差込がゲートに落ちるはず")

    legs, stakes, used = build_with_gate_fallback(shape, PLANS["C_hit"], po, pr,
                                                  n_entries=7)
    assert used in GATE_FALLBACK["C_hit"], "ゲートに落ちたのに現行へ戻していない"
    assert used.key == "C_hit", "代替が別の plan_key を名乗ると1レース2商品になる"
    assert (7, 3, 5) not in stakes
    assert len(stakes) == 12
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT


def test_no_fallback_when_the_insert_already_passes_the_gate():
    """差込のままゲートを通るなら、代替へ切り替えない。"""
    po, pr = _board({(7, 3, 5): 12.0})
    legs, stakes, used = build_with_gate_fallback(
        _shape(), PLANS["C_hit"], po, pr, n_entries=7)
    assert used is PLANS["C_hit"]
    assert (7, 3, 5) in stakes
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT


def test_insert_is_seven_car_only():
    """🔴 9車では差し込まない。測ったのは7車だけで、9車は `GATE_FALLBACK` も
    掛からない（ゲートに落ちたら在庫がそのまま消える）。"""
    po, pr = _board({(7, 3, 5): 6.2})
    _, st7, used7 = build_with_gate_fallback(_shape(), PLANS["C_hit"], po, pr, 7)
    _, st9, used9 = build_with_gate_fallback(_shape(), PLANS["C_hit"], po, pr, 9)
    assert used7.underband_min == 5.0 and (7, 3, 5) in st7
    assert used9.underband_min == 0.0 and (7, 3, 5) not in st9


# ───────────────────────── 版 ─────────────────────────

def test_rule_version_splits_when_the_floor_moves(monkeypatch):
    """🔴 下限を動かしたら7車の版が割れること（割れないと新旧の行が混ざる）。"""
    from dataclasses import replace
    before = rule_version(7)
    alt = dict(PLANS) | {"C_hit": replace(PLANS["C_hit"], underband_min=8.0)}
    monkeypatch.setattr("src.type_lab.PLANS", alt)
    assert rule_version(7) != before


def test_nine_car_rule_version_is_unchanged(monkeypatch):
    """🔴 9車の挙動は変えていないので、9車の版は割らないこと。"""
    from dataclasses import replace
    before9 = rule_version(9)
    alt = dict(PLANS) | {"C_hit": replace(PLANS["C_hit"], underband_min=8.0)}
    monkeypatch.setattr("src.type_lab.PLANS", alt)
    assert rule_version(9) == before9


# ───────────────────────── 文面 ─────────────────────────

def test_submission_band_table_matches_the_plans():
    """🔴 文面モジュールの帯の写しが `PLANS` とずれていないこと。

    `type_lab_submission.py` は標準ライブラリしか import できない（backend が
    ファイル読み込みで束縛する）ので帯を写している。ずれると**文面だけが**
    静かに事実と食い違う（買っていない1点を説明する／説明しない）。
    """
    from src.type_lab_submission import UNDERBAND_BANDS
    assert UNDERBAND_BANDS == {k: p.min_odds for k, p in PLANS.items()
                               if p.underband_min}


def test_the_copy_no_longer_claims_we_avoid_the_obvious():
    """🔴 「素直な決着は買わない」は 2026-09-08 から嘘なので書かないこと。"""
    from src.type_lab_submission import PLAN_BODIES
    assert "素直な決着は買わない" not in PLAN_BODIES["C_hit"]


def test_the_copy_is_written_only_when_the_point_is_actually_bought():
    """🔴 差込の一文は**買い目から導く**。差し込めなかったレース（実測23%）で
    固定文を出すと、買っていないものを説明することになる。"""
    from src.type_lab_submission import build_comment, underband_note

    with_ = [{"combo": "7-3-5", "stake": 3400, "pred_odds": 6.2},
             {"combo": "7-3-2", "stake": 1300, "pred_odds": 15.8}]
    without = [{"combo": "7-3-2", "stake": 1300, "pred_odds": 15.8},
               {"combo": "5-7-3", "stake": 1000, "pred_odds": 20.3}]
    assert underband_note("C_hit", with_)
    assert underband_note("C_hit", without) == ""
    # 差込を持たないプランには絶対に付かない
    for key in PLANS:
        if key != "C_hit":
            assert underband_note(key, with_) == ""

    body = build_comment("C_hit", "C", 7, 3, with_, "trifecta")
    assert "人気を集めている目も1点だけ押さえています" in body
    assert "人気を集めている" not in build_comment(
        "C_hit", "C", 7, 3, without, "trifecta")
