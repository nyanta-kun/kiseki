"""勝負アイコン「自信あり」の選定（1日1レース）の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件:

1. **三連単は対象外**（着順つきは確率モデルに載らない）
2. **1点でも盤面に無ければ EV を出さない**（部分計算で少点数ランクが有利になる）
3. **同値でも結果が決定的**（実行のたびに変わらない）
4. **入稿・承認の経路がランク名で決めていない**（旧仕様への逆戻り防止）
5. **型ラボは Σp（的中確率）で選ぶ**（2026-08-28〜）。EV と尺度が違うので混ぜない
"""
from __future__ import annotations

import json

import pytest
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
    """🔴 選定は**入稿の後**に走ること。先だと母集団の一部だけで選んでしまう。

    🔴 2026-08-30 に朝のバッチから旧ランク入稿・看板穴埋めを外したので
       （PR #380）、この順序を担保する場所は `type_lab_daily.sh` に移った。
       朝のバッチはその1本を呼ぶだけ。
    """
    sh = (REPO / "scripts" / "type_lab_daily.sh").read_text(encoding="utf-8")
    i_submit = sh.index("scripts/netkeirin_submit_type_lab.py")
    i_pick = sh.index("scripts/pick_confident_race_wt.py")
    assert i_pick > i_submit, "自信ありの選定が型ラボの入稿より前にあります"

    daily = (REPO / "scripts" / "daily_picks_wt.sh").read_text(encoding="utf-8")
    assert "scripts/type_lab_daily.sh" in daily, \
        "朝のバッチが型ラボを呼んでいません（自信ありも走らなくなる）"
    assert "scripts/pick_confident_race_wt.py" not in daily, \
        "朝のバッチが自信ありを二重に呼んでいます（型ラボ側で呼ぶ）"


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


# ───────────────────── 型ラボ（2026-08-28〜） ─────────────────────
#
# ユーザー決定: 「20,000円以上の払い戻しになりそうで、最も的中率が高そうなレース」。
#   候補 … pred_min_payout >= 20,000（**どの目が当たっても** 2万円以上）
#   順位 … Σp（買い目の的中確率の合計）が最大

def test_type_lab_hit_probability_sums_leg_probs():
    from src.confident_pick import type_lab_hit_probability

    legs = [{"prob": 0.12}, {"prob": 0.08}, {"prob": 0.05}]
    assert type_lab_hit_probability(legs, 30_000) == pytest.approx(0.25)


def test_type_lab_hit_probability_requires_min_payout():
    """🔴 「平均」ではなく「最低」想定払戻で候補を絞る。

    平均で見ると**当たった目によっては2万円に届かない**商品に
    「自信あり」が付き、アイコンの約束と食い違う。
    """
    from src.confident_pick import TYPE_LAB_MIN_PAYOUT, type_lab_hit_probability

    legs = [{"prob": 0.5}]
    assert type_lab_hit_probability(legs, TYPE_LAB_MIN_PAYOUT) is not None
    assert type_lab_hit_probability(legs, TYPE_LAB_MIN_PAYOUT - 1) is None
    assert type_lab_hit_probability(legs, None) is None


def test_type_lab_hit_probability_never_sums_partially():
    """🔴 一部だけで足さない（点数の多い商品が不当に低く出る）。"""
    from src.confident_pick import type_lab_hit_probability

    assert type_lab_hit_probability([{"prob": 0.2}, {"prob": None}], 30_000) is None
    assert type_lab_hit_probability([{"prob": 0.2}, {}], 30_000) is None
    assert type_lab_hit_probability([], 30_000) is None


def test_type_lab_pick_takes_max_hit_probability(monkeypatch):
    """型ラボの行があれば Σp 最大の1件を選ぶ（EV 経路は使わない）。"""
    from scripts import pick_confident_race_wt as m

    rows = [
        # 最低想定払戻が足りない → Σp が最大でも選ばれない
        {"race_key": "20260829_11_01", "rank_key": "C_hit", "venue_name": "A",
         "race_no": 1, "legs": [{"prob": 0.9}], "pred_min_payout": 19_000},
        {"race_key": "20260829_11_02", "rank_key": "B_hit", "venue_name": "A",
         "race_no": 2, "legs": [{"prob": 0.20}, {"prob": 0.10}],
         "pred_min_payout": 28_000},
        {"race_key": "20260829_11_03", "rank_key": "D_hit", "venue_name": "A",
         "race_no": 3, "legs": [{"prob": 0.15}], "pred_min_payout": 35_000},
    ]
    monkeypatch.setattr(m, "_load_type_lab", lambda date: rows)

    def _never(*a, **k):
        raise AssertionError("型ラボがあるのに EV 経路を使った")

    monkeypatch.setattr(m, "_load_alive", _never)
    monkeypatch.setattr(m, "race_expected_value", _never)

    assert m.pick("2026-08-29", dry_run=True) == ("20260829_11_02", "B_hit")


def test_type_lab_and_legacy_scores_are_never_mixed(monkeypatch):
    """🔴 EV と Σp は尺度が違う。型ラボが1件でもあれば EV 経路は見ない。"""
    from scripts import pick_confident_race_wt as m

    monkeypatch.setattr(m, "_load_type_lab", lambda date: [])
    monkeypatch.setattr(m, "_load_alive", lambda date: [
        {"race_key": "20260829_11_09", "rank_key": "7C", "venue_name": "A",
         "race_no": 9, "bet_detail": "{}"}])
    monkeypatch.setattr(m, "race_expected_value", lambda rk, bd: 1.5)
    # 型ラボが0件なら従来どおり EV 経路へ落ちる
    assert m.pick("2026-08-29", dry_run=True) == ("20260829_11_09", "7C")
