"""勝負アイコン「自信あり」の選定（1日1レース）の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件:

1. **三連単は対象外**（着順つきは確率モデルに載らない）
2. **1点でも盤面に無ければ EV を出さない**（部分計算で少点数ランクが有利になる）
3. **同値でも結果が決定的**（実行のたびに変わらない）
4. **入稿・承認の経路がランク名で決めていない**（旧仕様への逆戻り防止）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.confident_pick import (  # noqa: E402
    expected_value_from_lines, pick_best,
)

TRIO = "3連複"


def _lines(*specs):
    """(combo, stake) から bet_detail の lines を作る。"""
    return [{"bet_type": TRIO, "combo": c, "stake": s} for c, s in specs]


def _board(**kw):
    return {frozenset(int(c) for c in k.split("_")): v for k, v in kw.items()}


def test_ev_is_expected_return_ratio():
    """EV = Σ(p × 賭け金 × オッズ) ÷ 総賭け金。"""
    lines = _lines(("1=2=3", 5000), ("1=2=4", 5000))
    board = _board(**{"1_2_3": 10.0, "1_2_4": 20.0})
    probs = {frozenset({1, 2, 3}): 0.10, frozenset({1, 2, 4}): 0.02}
    # (0.10*5000*10 + 0.02*5000*20) / 10000 = (5000 + 2000)/10000
    assert expected_value_from_lines(lines, board, probs) == 0.7


def test_trifecta_lines_are_out_of_scope():
    """🔴 三連単（"-" 区切り）は None。混ぜると尺度の違うものを比べることになる。"""
    lines = [{"bet_type": "3連単", "combo": "1-2-3", "stake": 10000}]
    assert expected_value_from_lines(lines, {}, {}) is None
    # bet_type が三連複でも combo が着順つきなら弾く
    lines2 = _lines(("1-2-3", 10000))
    assert expected_value_from_lines(lines2, {}, {}) is None


def test_missing_point_makes_ev_none():
    """🔴 一部だけで計算しない（点数の少ないランクが不当に高く出る）。"""
    lines = _lines(("1=2=3", 5000), ("1=2=4", 5000))
    board = _board(**{"1_2_3": 10.0})            # 1=2=4 が無い
    probs = {frozenset({1, 2, 3}): 0.1, frozenset({1, 2, 4}): 0.02}
    assert expected_value_from_lines(lines, board, probs) is None


def test_zero_or_missing_odds_makes_ev_none():
    lines = _lines(("1=2=3", 5000))
    probs = {frozenset({1, 2, 3}): 0.1}
    assert expected_value_from_lines(lines, _board(**{"1_2_3": 0.0}), probs) is None


def test_empty_lines_is_none():
    assert expected_value_from_lines([], {}, {}) is None


def test_pick_best_takes_the_max():
    got = pick_best([("A", "7S", 0.8), ("B", "7A", 1.2), ("C", "7C", 0.9)])
    assert got == ("B", "7A")


def test_pick_best_ignores_none():
    got = pick_best([("A", "7S", None), ("B", "7A", 0.5)])
    assert got == ("B", "7A")


def test_pick_best_is_none_when_nothing_usable():
    assert pick_best([("A", "7S", None)]) is None
    assert pick_best([]) is None


def test_pick_best_is_deterministic_on_ties():
    """🔴 同値のとき入力順で結果が変わらないこと。

    DB の並びが変わった日に選ばれるレースが変わると、原因の追跡ができない。
    """
    a = pick_best([("20260813_11_05", "7A", 1.0), ("20260813_11_02", "7C", 1.0)])
    b = pick_best([("20260813_11_02", "7C", 1.0), ("20260813_11_05", "7A", 1.0)])
    assert a == b == ("20260813_11_02", "7C")


def test_race_expected_value_rejects_broken_detail():
    from src.confident_pick import race_expected_value
    assert race_expected_value("20260813_11_01", None) is None
    assert race_expected_value("20260813_11_01", "{壊れ") is None
    assert race_expected_value("20260813_11_01", json.dumps({"lines": []})) is None


def test_daily_batch_picks_after_submitting():
    """🔴 選定は**入稿の後**に走ること。先だと母集団の一部だけで選んでしまう。"""
    sh = (REPO / "scripts" / "daily_picks_wt.sh").read_text(encoding="utf-8")
    i_rank = sh.index("scripts/netkeirin_submit_wt.py")
    i_fill = sh.index("scripts/submit_marquee_wt.py")
    i_pick = sh.index("scripts/pick_confident_race_wt.py")
    assert i_pick > i_rank, "自信ありの選定がランク入稿より前にあります"
    assert i_pick > i_fill, "自信ありの選定が看板穴埋めより前にあります"


def test_wave_submit_does_not_pick_again():
    """🔴 昼・夕の波で選び直さないこと（1日1件が壊れる）。"""
    sh = (REPO / "scripts" / "wave_submit_wt.sh").read_text(encoding="utf-8")
    assert "pick_confident_race_wt.py" not in sh, (
        "波の入稿でも自信ありを選び直しています。当日2回目を選ぶと1日1件が壊れます")


def test_picker_clears_the_day_before_setting_one():
    """🔴 「当日を全部 false → 1件 true」の順であること（1日1件の担保）。"""
    src = (REPO / "scripts" / "pick_confident_race_wt.py").read_text(encoding="utf-8")
    i_false = src.index("SET is_confident = FALSE")
    i_true = src.index("SET is_confident = TRUE")
    assert i_false < i_true, "先に1件立ててから全消ししています（毎回0件になります）"
