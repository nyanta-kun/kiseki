"""日次レビュー（`scripts/daily_review_wt.py`）の分類ロジックの回帰テスト。

この道具の出力は**そのまま改善判断の入力になる**ので、分類が静かに壊れると
「毎日この型で外れている」という誤った積み上げができあがる。DBに触らない
純関数だけをここで固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scripts.daily_review_wt import _axes_and_legs, _cf_return, classify  # noqa: E402

#: 指数（全体順位 = 1,2,3,… の順に高い）
P3 = {1: 0.90, 2: 0.80, 3: 0.50, 4: 0.40, 5: 0.30, 6: 0.20, 7: 0.10}


def _pick(combo, n=3, hit=0):
    return {"pred_combo": combo, "n_combos": n, "hit": hit}


# ── 軸と相手の復元 ────────────────────────────────────────────────────

def test_axes_from_trio_are_the_common_pair():
    axes, legs = _axes_and_legs([(1, 2, 3), (1, 2, 4), (1, 2, 5)], "trio")
    assert axes == (1, 2)
    assert legs == {3, 4, 5}


def test_axes_from_trifecta_formation():
    axes, legs = _axes_and_legs([(5, 2, 3), (5, 2, 4)], "trifecta")
    assert axes == (5, 2)
    assert legs == {3, 4}


def test_axes_none_when_trifecta_head_is_not_fixed():
    """1着が固定されていない買い目（7H2 の倍購入型）では軸を主張しない。"""
    axes, _ = _axes_and_legs([(5, 2, 3), (2, 5, 3)], "trifecta")
    assert axes is None


# ── 外れの型 ──────────────────────────────────────────────────────────

def test_hit_is_reported_as_hit():
    miss, fix, _ = classify(_pick("1=2-3,4,5", hit=1), [1, 2, 3], P3)
    assert miss == "HIT" and fix == "-"


def test_leg_miss_when_axes_came_but_third_was_not_bought():
    # 軸1,2 は3着内。3着目は 6（指数6番手）で買い目(3,4,5)の外。
    miss, fix, note = classify(_pick("1=2-3,4,5"), [1, 2, 6], P3)
    assert miss == "LEG_MISS"
    assert fix == "LEGS_WIDER"
    assert "n=6" in note


def test_axis2_out_is_separated_from_axis1_out():
    """🔴 「軸2だけ来ず」と「軸1が飛んだ」を混ぜないこと。

    前者は軸の選び方で届きうるが、後者は朝の指数からは取りようがない。
    混ぜると「軸を替えれば当たった」の件数が水増しされる。
    """
    m1, f1, _ = classify(_pick("1=2-3,4,5"), [1, 3, 6], P3)   # 軸1(1)は来た・軸2(2)が消えた
    assert m1 == "AXIS2_OUT" and f1 == "AXIS2_SWAP"
    m2, f2, _ = classify(_pick("1=2-3,4,5"), [2, 3, 6], P3)   # 軸1(1)が消えた
    assert m2 == "AXIS1_OUT" and f2 == "UNREACHABLE"


def test_both_axis_out_is_unreachable():
    miss, fix, _ = classify(_pick("1=2-3,4,5"), [3, 4, 5], P3)
    assert miss == "BOTH_AXIS_OUT" and fix == "UNREACHABLE"


def test_order_miss_is_detected_for_trifecta():
    """3車は合っていて着順だけ違う＝三連複なら的中していた型。"""
    miss, fix, note = classify(_pick("三単:1-2-3,1-2-4"), [2, 1, 3], P3)
    assert miss == "ORDER_MISS" and fix == "TRIO_INSTEAD"
    assert "k=2" in note


def test_no_result_when_order_incomplete():
    miss, _, _ = classify(_pick("1=2-3,4,5"), [1, 2], P3)
    assert miss == "NO_RESULT"


# ── 反実仮想の採算（この道具の歯止め）──────────────────────────────

def test_cf_return_divides_by_the_widened_point_count():
    """🔴 反実仮想は**広げた点数で割る**こと。

    予算枠方式なので点数が増えれば1点あたりが薄くなる。配当だけを見て
    「広げれば当たった」と積み上げると、当てても投資割れの変更を推してしまう。
    """
    # 配当 280円（2.8倍）を5点に広げて的中 → 1レース 0.56倍＝投資割れ。
    assert _cf_return("LEGS_ALL", "n=7;k=5", 280) == 0.56


def test_cf_return_is_none_when_unreachable():
    assert _cf_return("UNREACHABLE", "", 12250) is None
    assert _cf_return("-", "", 0) is None


def test_cf_return_uses_leg_count_for_trio_instead():
    # 三連単4点 → 同じ相手数の三連複4点。460円なら 1.15倍。
    assert _cf_return("TRIO_INSTEAD", "k=4", 460) == 1.15
