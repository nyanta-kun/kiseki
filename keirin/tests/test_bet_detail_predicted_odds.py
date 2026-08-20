"""板に無いオッズを予測値で埋める処理の検査（2026-08-12）。

板（`wt_odds` / 朝の `wt_odds_snapshot`）は買った目を必ずしも網羅せず、
欠けた点は `odds: null` で保存されて Web では「オッズ未取得」になっていた
（最低払戻も期待値も出せない）。構造モデルの予測オッズで表示だけ埋める。

🔴 **入稿時は予測オッズが主**（2026-08-21 に板優先から反転）。配分も足切りも
   予測オッズで決めているため、表示だけ板だと根拠と突き合わせられない。
   板へ落ちるのは予測を作れない目（三連単・7車9車以外）だけ。
🔴 **`odds_source` の記録は落とさない。** 表示では区別しなくなったが、
   三連単だけ板由来で残るので、混在を数えられないと検証ができない。
⚠️ **過去分のバックフィル（`fill_lines`）は板を上書きしない。** あちらは
   既に入稿済みの記録で、当時実際に付いていた値だから。
⚠️ **三連単は埋めない。** このモデルが予測するのは三連複だけで、
   着順の分だけ別物になる。作れないものを作らない。
"""
from __future__ import annotations

import json

import scripts.backfill_bet_detail_predicted_odds as bf
from scripts.netkeirin_submit_wt import BetLeg, build_bet_detail

# 三連複・軸2車流し（1-2 軸、相手 3/4）
LEGS = [BetLeg(bet_kind="trio_axis2", groups=[[1], [2], [3, 4]], stake_per_line=1000)]


def _lines(detail_json: str) -> list[dict]:
    return json.loads(detail_json)["lines"]


# ---------------------------------------------------------------------------
# 入稿時（build_bet_detail）
# ---------------------------------------------------------------------------

def test_予測があれば予測を使い印はpredicted():
    """🔴 2026-08-21 に優先順位を反転（板優先 → 予測優先）。

    配分（`landing_weights`）も 1.5倍の足切り（`_expected_payout_floor_for`）も
    予測オッズで決めているので、表示だけ板だと確認画面の数字と判断根拠が
    突き合わせられない。
    """
    out = _lines(build_bet_detail(
        LEGS, "odds",
        odds={frozenset({1, 2, 3}): 5.5, frozenset({1, 2, 4}): 9.9},
        predicted_odds={frozenset({1, 2, 3}): 111.0, frozenset({1, 2, 4}): 222.0},
    ))
    assert [x["odds"] for x in out] == [111.0, 222.0], "板が予測を上書きしています"
    assert {x["odds_source"] for x in out} == {"predicted"}


def test_予測を作れない点だけ板へ落ちる():
    """三連単・7車9車以外は予測を作れない。そこだけ板を使う。"""
    out = _lines(build_bet_detail(
        LEGS, "odds",
        odds={frozenset({1, 2, 3}): 5.5, frozenset({1, 2, 4}): 9.9},
        predicted_odds={frozenset({1, 2, 4}): 12.34},
    ))
    by_combo = {x["combo"]: x for x in out}
    assert by_combo["1=2=3"]["odds"] == 5.5
    assert by_combo["1=2=3"]["odds_source"] == "board"
    assert by_combo["1=2=4"]["odds"] == 12.3  # 小数1桁へ丸める
    assert by_combo["1=2=4"]["odds_source"] == "predicted"


def test_予測にも無ければ不明のまま():
    """作れないものを作らない。null のままにして『未取得』と出させる。"""
    out = _lines(build_bet_detail(LEGS, "odds", odds={}, predicted_odds={}))
    assert all(x["odds"] is None for x in out)
    assert all(x["odds_source"] is None for x in out)


def test_予測盤面を渡さなければ従来どおり():
    """既存の呼び出し（predicted_odds なし）が壊れないこと。"""
    out = _lines(build_bet_detail(LEGS, "odds", odds={frozenset({1, 2, 3}): 5.5}))
    by_combo = {x["combo"]: x for x in out}
    assert by_combo["1=2=3"]["odds"] == 5.5
    assert by_combo["1=2=4"]["odds"] is None


# ---------------------------------------------------------------------------
# 過去分のバックフィル
# ---------------------------------------------------------------------------

def test_バックフィルは三連単を埋めない():
    """🔴 モデルが予測するのは三連複だけ。区切り文字が券種の区別そのもの。"""
    detail = {"total": 2000, "lines": [
        {"bet_type": "3連単", "combo": "1-2-4", "stake": 1000, "odds": None},
        {"bet_type": "3連複", "combo": "1=2=4", "stake": 1000, "odds": None},
    ]}
    out, n = bf.fill_lines(detail, {frozenset({1, 2, 4}): 8.8})
    assert n == 1
    assert out["lines"][0]["odds"] is None, "三連単を三連複の盤面で埋めています"
    assert out["lines"][1]["odds"] == 8.8
    assert out["lines"][1]["odds_source"] == "predicted"


def test_バックフィルは既存の板を上書きせず印だけ補う():
    detail = {"lines": [{"bet_type": "3連複", "combo": "1=2=3", "stake": 1000, "odds": 4.2}]}
    out, n = bf.fill_lines(detail, {frozenset({1, 2, 3}): 99.9})
    assert n == 0
    assert out["lines"][0]["odds"] == 4.2
    assert out["lines"][0]["odds_source"] == "board", "過去分に印が付いていません"


def test_バックフィルは盤面に無ければ触らない():
    detail = {"lines": [{"bet_type": "3連複", "combo": "1=2=3", "stake": 1000, "odds": None}]}
    out, n = bf.fill_lines(detail, {})
    assert n == 0 and out["lines"][0]["odds"] is None


def test_combo_keyは券種を取り違えない():
    assert bf._combo_key("1=2=4") == frozenset({1, 2, 4})
    assert bf._combo_key("1-2-4") is None, "三連単を三連複として解釈しています"
    assert bf._combo_key("1=2") is None
    assert bf._combo_key("こわれた") is None
