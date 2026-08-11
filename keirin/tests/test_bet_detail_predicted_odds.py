"""板に無いオッズを予測値で埋める処理の検査（2026-08-12）。

板（`wt_odds` / 朝の `wt_odds_snapshot`）は買った目を必ずしも網羅せず、
欠けた点は `odds: null` で保存されて Web では「オッズ未取得」になっていた
（最低払戻も期待値も出せない）。構造モデルの予測オッズで表示だけ埋める。

🔴 **埋めた点は必ず `odds_source="predicted"` として区別する。**
   板の値と同じ顔で出すと「実際に付いていたオッズ」と読まれる。
🔴 **板を上書きしない。** 板があるならそれが実際に付いていた値。
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

def test_板があるときは板を使い印はboard():
    out = _lines(build_bet_detail(
        LEGS, "odds",
        odds={frozenset({1, 2, 3}): 5.5, frozenset({1, 2, 4}): 9.9},
        predicted_odds={frozenset({1, 2, 3}): 111.0, frozenset({1, 2, 4}): 222.0},
    ))
    assert [x["odds"] for x in out] == [5.5, 9.9], "予測が板を上書きしています"
    assert {x["odds_source"] for x in out} == {"board"}


def test_板に無い点だけ予測で埋める():
    out = _lines(build_bet_detail(
        LEGS, "odds",
        odds={frozenset({1, 2, 3}): 5.5},
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
