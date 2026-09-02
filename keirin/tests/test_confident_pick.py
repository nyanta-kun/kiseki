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


# ───────────────────── 型ラボ（2026-09-02〜） ─────────────────────
#
# ユーザー指示 2026-09-02:
#   「夕方くらいまでのレースのうち、合成が3倍以上で期待値が最も高いレース」
#     候補 … 発走 JST < 18時 ∧ 合成オッズ >= 3.0倍
#     順位 … EV = Σ(確率×賭け金×予測オッズ) ÷ Σ賭け金 が最大
#
# 🔴 2026-08-28 の「pred_min_payout >= 20,000 → Σp 最大」を**置き換えた**。
#    epoch 0 = 1970-01-01 09:00 JST なので、以下では時刻を 9*3600 秒単位で作る。

_H = 3600


def _legs(prob, odds, stake=5_000, n=2):
    return [{"prob": prob, "stake": stake, "pred_odds": odds} for _ in range(n)]


def test_synthetic_odds_is_allocation_free():
    """🔴 合成オッズは買い目の集合だけで決まる（賭け金に依らない）。"""
    from src.confident_pick import synthetic_odds

    assert synthetic_odds([{"pred_odds": 10}, {"pred_odds": 10}]) == pytest.approx(5.0)
    # 賭け金が違っても同じ値
    assert synthetic_odds([{"pred_odds": 10, "stake": 100},
                           {"pred_odds": 10, "stake": 9_900}]) == pytest.approx(5.0)


def test_synthetic_odds_never_uses_partial_legs():
    """🔴 1点でも欠けたら None。残りだけで合成すると欠測レースほど大きく出る。"""
    from src.confident_pick import synthetic_odds

    assert synthetic_odds([{"pred_odds": 10}, {"pred_odds": None}]) is None
    assert synthetic_odds([{"pred_odds": 10}, {}]) is None
    assert synthetic_odds([{"pred_odds": 10}, {"pred_odds": 0}]) is None
    assert synthetic_odds([]) is None


def test_legs_expected_value_weights_by_stake():
    from src.confident_pick import legs_expected_value

    legs = [{"prob": 0.1, "stake": 5_000, "pred_odds": 10},
            {"prob": 0.1, "stake": 5_000, "pred_odds": 20}]
    assert legs_expected_value(legs) == pytest.approx(1.5)
    assert legs_expected_value([{"prob": 0.2, "stake": 100, "pred_odds": 10},
                                {"prob": None, "stake": 100, "pred_odds": 10}]) is None
    assert legs_expected_value([]) is None


def test_confident_score_excludes_evening_and_later():
    """🔴 「夕方くらいまで」= 発走 18時前。境界そのものは候補外。"""
    from src.confident_pick import CONFIDENT_BEFORE_HOUR, type_lab_confident_score

    assert CONFIDENT_BEFORE_HOUR == 18
    legs = _legs(0.1, 10)                      # 合成 5.0倍
    assert type_lab_confident_score(legs, 0) is not None            # 09:00 JST
    assert type_lab_confident_score(legs, 8 * _H) is not None       # 17:00 JST
    assert type_lab_confident_score(legs, 9 * _H) is None           # 18:00 JST（境界は外）
    assert type_lab_confident_score(legs, 13 * _H) is None          # 22:00 JST


def test_confident_score_requires_synthetic_odds_floor():
    """🔴 合成3倍以上。`B_hit` は sigma_max=1/3 で 3.00 に張り付くので `>=`。"""
    from src.confident_pick import CONFIDENT_MIN_SYNTH_ODDS, type_lab_confident_score

    assert CONFIDENT_MIN_SYNTH_ODDS == 3.0
    # 2点とも6.0倍 → 合成ちょうど3.0倍。「3倍以上」なので候補に入る
    assert type_lab_confident_score(_legs(0.1, 6.0), 0) is not None
    # 2点とも5.9倍 → 合成 2.95倍
    assert type_lab_confident_score(_legs(0.1, 5.9), 0) is None


def test_confident_score_drops_races_without_start_time():
    """🔴 発走時刻が読めないレースは候補にしない。

    ここで「分からないものは通す」を採ると、時刻の取れない開催だけが
    終日どこからでも選ばれて時刻の条件が意味を失う。
    """
    from src.confident_pick import type_lab_confident_score

    legs = _legs(0.1, 10)
    assert type_lab_confident_score(legs, None) is None
    assert type_lab_confident_score(legs, "") is None
    assert type_lab_confident_score(legs, "abc") is None


def test_confident_before_hour_matches_submission_wave():
    """🔴 「夕方」の境界は入稿の波（`meeting_wave`）と同じ値でなければならない。

    ずれると「夕の波で入稿したのに夕方扱いされない」レースが出る。
    """
    from src.confident_pick import CONFIDENT_BEFORE_HOUR
    from src.meeting_wave import NIGHT_FROM_HOUR

    assert CONFIDENT_BEFORE_HOUR == NIGHT_FROM_HOUR


def test_type_lab_pick_takes_max_ev_among_eligible(monkeypatch):
    """型ラボの行があれば候補内で EV 最大の1件を選ぶ（旧 EV 経路は使わない）。"""
    from scripts import pick_confident_race_wt as m

    rows = [
        # EV は最大だが 22:00 発走 → 候補外
        {"race_key": "20260902_11_01", "rank_key": "C_hit", "venue_name": "A",
         "race_no": 1, "legs": _legs(0.30, 10), "start_at": 13 * _H},
        # 合成 2.5倍（5.0倍×2点）→ 候補外
        {"race_key": "20260902_11_02", "rank_key": "E_hit", "venue_name": "A",
         "race_no": 2, "legs": _legs(0.25, 5.0), "start_at": 0},
        # 候補。EV = 0.10 × 20 = 2.0
        {"race_key": "20260902_11_03", "rank_key": "B_hit", "venue_name": "A",
         "race_no": 3, "legs": _legs(0.10, 20), "start_at": 0},
        # 候補。EV = 0.12 × 10 = 1.2
        {"race_key": "20260902_11_04", "rank_key": "D_hit", "venue_name": "A",
         "race_no": 4, "legs": _legs(0.12, 10), "start_at": 8 * _H},
    ]
    monkeypatch.setattr(m, "_load_type_lab", lambda date: rows)

    def _never(*a, **k):
        raise AssertionError("型ラボがあるのに旧 EV 経路を使った")

    monkeypatch.setattr(m, "_load_alive", _never)
    monkeypatch.setattr(m, "race_expected_value", _never)

    assert m.pick("2026-09-02", dry_run=True) == ("20260902_11_03", "B_hit")


def test_type_lab_and_legacy_scores_are_never_mixed(monkeypatch):
    """🔴 新旧の EV は計算方法も母集団も違う。型ラボが1件でもあれば旧経路は見ない。"""
    from scripts import pick_confident_race_wt as m

    monkeypatch.setattr(m, "_load_type_lab", lambda date: [])
    monkeypatch.setattr(m, "_load_alive", lambda date: [
        {"race_key": "20260829_11_09", "rank_key": "7C", "venue_name": "A",
         "race_no": 9, "bet_detail": "{}"}])
    monkeypatch.setattr(m, "race_expected_value", lambda rk, bd: 1.5)
    # 型ラボが0件なら従来どおり旧 EV 経路へ落ちる
    assert m.pick("2026-08-29", dry_run=True) == ("20260829_11_09", "7C")


def test_alive_filter_qualifies_status_column():
    """🔴 `status` は `wt_races` にもあるので必ず `s.` で修飾する。

    2026-09-02 に発走時刻のため `wt_races` を JOIN したところ、裸の `status` が
    `AmbiguousColumn` で落ちた。DB を叩かないと出ない類なので SQL の形で固定する。
    """
    import inspect

    from scripts import pick_confident_race_wt as m

    assert "s.status" in m._ALIVE
    for fn in (m._load_alive, m._load_type_lab):
        src = inspect.getsource(fn)
        assert "netkeirin_submissions s " in src, fn.__name__
        # 別名を付けずに裸の列を並べていないこと（SELECT 側も修飾する）
        assert "SELECT race_key" not in src, fn.__name__
