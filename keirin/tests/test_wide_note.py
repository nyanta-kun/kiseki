"""総流し時の「ワイド1点も見比べて」の一文（2026-08-14・ユーザー要望）。

## なぜ正しいと言えるか

軸2車から**残り全車へ流す**三連複は、「軸2車がともに3着以内」でどれかの目に
必ず当たる。これはワイド（軸1-軸2）の的中条件と**厳密に一致**する。
控除率が同じでも人気の偏りでワイドのほうが高くつくことがあるので、
見比べる価値がある。

## 🔴 絞り買いには出してはいけない

相手を絞っている買い目（7C/9C の足切り・7B の3点）は、軸2車が3着内でも相手が
外れれば当たらない。そこへ同じ文を出すと**「必ず的中」が嘘になる**。
判定は「相手が残り全車か」（`n_legs == n_cars - 2`）だけで行う。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.race_shape import wide_note_text  # noqa: E402


def test_full_spread_gets_the_note():
    note = wide_note_text(1, 5, 5, 7)          # 7車・相手5＝総流し
    assert "総流し" in note
    assert "ワイド1-5" in note
    assert "◎1番と○5番" in note


def test_nine_car_full_spread_also_gets_it():
    assert wide_note_text(3, 1, 7, 9)          # 9車・相手7＝総流し


def test_narrowed_legs_get_nothing():
    """🔴 絞り買いには出さない（「必ず的中」が嘘になる）。"""
    assert wide_note_text(1, 5, 4, 7) == ""    # 7C の足切り
    assert wide_note_text(1, 5, 3, 7) == ""    # 7B の3点
    assert wide_note_text(3, 1, 5, 9) == ""    # 9C の足切り


def test_missing_car_breaks_the_full_spread():
    """欠車で相手が減れば総流しではなくなる（そのときは出さない）。"""
    assert wide_note_text(1, 5, 4, 7) == ""


def test_degenerate_inputs_are_safe():
    assert wide_note_text(1, 2, 0, 2) == ""
    assert wide_note_text(1, 2, 0, 0) == ""


def test_templates_carry_the_placeholder():
    """🔴 テンプレートに `{wide_note}` が残っていること。

    消すと総流しの説明が出なくなるが、**エラーにはならない**ので気づけない。
    """
    from scripts.netkeirin_submit_wt import (
        _DEFAULT_COMMENT_TEMPLATE, _MARQUEE_COMMENT_TEMPLATE,
    )
    assert "{wide_note}" in _DEFAULT_COMMENT_TEMPLATE
    assert "{wide_note}" in _MARQUEE_COMMENT_TEMPLATE


def test_placeholder_is_substituted_away_for_narrow_bets():
    """絞り買いでは `{wide_note}` が空文字で消えること（プレースホルダを残さない）。"""
    from scripts.netkeirin_submit_wt import (
        _DEFAULT_COMMENT_TEMPLATE, _apply_template,
    )
    out = _apply_template(
        _DEFAULT_COMMENT_TEMPLATE, venue_name="松山", race_no=9, rank_key="7C",
        target_date="2026-08-14", axis1=1, axis2=5, shape="", shape_note="",
        stake_note="", race_type="", wide_note=wide_note_text(1, 5, 4, 7),
    )
    assert "{wide_note}" not in out
    assert "ワイド" not in out
