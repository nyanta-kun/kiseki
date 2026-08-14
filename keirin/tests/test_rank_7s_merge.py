"""7SS / 7S / 7A の統合（RANK_7S 1本化）の土台テスト（2026-08-14）。

## なぜ統合するか

3つは**買い目構造が完全に同一**（三連複 軸2車+5点流し）で、違うのはゲートの
通り方だけ。picks_history の全live記録（n=7,461・32ヶ月）では

    7SS ROI 79.0% / 7S 79.7% / 7A 85.8%

と**設計と逆順**（境界ランクの 7A が最良）。差はいずれも有意でなく
（7A −(7SS+7S) = +6.3pt・95%CI [-0.7, +13.5]）、設計どおりの順序になった月は
32ヶ月中7（偶然なら5.3）。払戻中央値・ガミ率・2万円超率まで一致していた。

## ここで守ること

統合は**ラベルの付け替え**であって選別の変更ではない。買うレースが1件も
増減しないこと（＝3つが排他で、和集合が統合後の母集団になること）を固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    RANK_7S_AXIS_SUM_MAX, RANK_7S_ENTROPY_MAX, rank_7a_daily_select,
    rank_7s_daily_select, rank_7s_merged_daily_select, rank_7ss_daily_select,
)

_PASS_A = RANK_7S_AXIS_SUM_MAX - 0.1      # axis_sum 合格
_FAIL_A = RANK_7S_AXIS_SUM_MAX + 0.1      # axis_sum 不合格
_PASS_E = RANK_7S_ENTROPY_MAX - 0.1       # entropy 合格
_FAIL_E = RANK_7S_ENTROPY_MAX + 0.1       # entropy 不合格


def _c(key, axis_sum, entropy, overlap=0, same_line=False):
    return {"race_key": key, "axis_sum": axis_sum, "entropy": entropy,
            "wt_overlap_n": overlap, "same_line": same_line}


#: 4象限 × 同一ラインの有無 + 対象外(overlap=2)
CANDS = [
    _c("s", _PASS_A, _PASS_E),                       # 旧 7S
    _c("a", _FAIL_A, _PASS_E),                       # 旧 7A
    _c("ss", _PASS_A, _FAIL_E, same_line=True),      # 旧 7SS
    _c("e_noline", _PASS_A, _FAIL_E, same_line=False),  # どこにも入らない
    _c("both_fail", _FAIL_A, _FAIL_E, same_line=True),  # どこにも入らない
    _c("agree", _PASS_A, _PASS_E, overlap=2),        # ◎◯完全一致＝対象外
]


def test_the_three_ranks_are_disjoint():
    """🔴 排他であること（重なると統合で二重計上になる）。"""
    picked = [{c["race_key"] for c in f(CANDS)}
              for f in (rank_7s_daily_select, rank_7a_daily_select,
                        rank_7ss_daily_select)]
    for i in range(len(picked)):
        for j in range(i + 1, len(picked)):
            assert not (picked[i] & picked[j]), f"重なり: {picked[i] & picked[j]}"


def test_merged_is_exactly_the_union():
    """🔴 統合後の母集団＝3つの和集合（買うレースが1件も増減しない）。"""
    union = set()
    for f in (rank_7s_daily_select, rank_7a_daily_select, rank_7ss_daily_select):
        union |= {c["race_key"] for c in f(CANDS)}
    assert {c["race_key"] for c in rank_7s_merged_daily_select(CANDS)} == union
    assert union == {"s", "a", "ss"}


def test_merge_does_not_pick_up_the_uncovered_population():
    """⚠️ 「entropy 不合格 ∧ 同一ラインでない」「両方不合格」は**今も対象外**。

    統合はゲートを外すことではない。ここを拾い始めると母集団が変わり、
    過去実績との比較が成立しなくなる（撤廃の是非は別途 walk-forward で測る）。
    """
    got = {c["race_key"] for c in rank_7s_merged_daily_select(CANDS)}
    assert "e_noline" not in got
    assert "both_fail" not in got
    assert "agree" not in got, "◎◯完全一致は ROI 74.6% で控除率の壁の下"


def test_merge_raises_when_the_selectors_overlap():
    """🔴 排他が壊れたら**黙って二重計上せず落ちる**こと。

    将来どれかのゲートを緩めたとき、和集合が重なりうる。そのとき
    投資額と件数が静かに二重になるのが最悪の壊れ方なので、例外にする。
    """
    same = [_c("dup", _PASS_A, _PASS_E), _c("dup", _FAIL_A, _PASS_E)]
    with pytest.raises(AssertionError, match="排他のはず"):
        rank_7s_merged_daily_select(same)


def test_merge_reuses_the_existing_selectors():
    """🔴 条件を書き直していないこと（写すと片方だけ直せてしまう）。"""
    import inspect
    src = inspect.getsource(rank_7s_merged_daily_select)
    for name in ("rank_7s_daily_select", "rank_7a_daily_select",
                 "rank_7ss_daily_select"):
        assert name in src, f"{name} を呼んでいない"
    assert "RANK_7S_AXIS_SUM_MAX" not in src, "閾値を再実装している"
    assert "RANK_7S_ENTROPY_MAX" not in src, "閾値を再実装している"
