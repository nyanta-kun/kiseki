"""入稿する買い目のオッズは**予測オッズだけ**で作る（2026-08-12 / 2026-08-26 改定）。

🔴 **板（`wt_odds` / 朝の `wt_odds_snapshot`）は入稿経路から一切参照しない**
   （2026-08-26・ユーザー指示）。`build_bet_detail` / `build_bet_lines` から
   板の引数そのものを外してあるので、混ぜたくても混ぜられない。

経緯: 2026-08-21 に「予測オッズを優先し、作れない目だけ板」へ反転したが、
**三連単には予測盤面を渡していなかった**ので実際には板のままだった
（8/22〜8/26 の板由来 89点はすべて 7H1 / 7T1）。三連単の予測オッズは
`src.odds_prediction_tf` に既にあり、候補生成では使っていた。

🔴 **`odds_source` の列は残す。** 過去分には "board" の行があり、
   混在した期間を後から数えられないと検証ができない。
⚠️ **過去分のバックフィル（`fill_lines`）は板を上書きしない。** あちらは
   既に入稿済みの記録で、当時実際に付いていた値だから。
⚠️ **バックフィルは三連単を埋めない**（あちらは三連複モデルしか持たない）。
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

def test_予測オッズがそのまま記録される():
    out = _lines(build_bet_detail(
        LEGS, "predicted",
        predicted_odds={frozenset({1, 2, 3}): 111.0, frozenset({1, 2, 4}): 222.0},
    ))
    assert [x["odds"] for x in out] == [111.0, 222.0]
    assert {x["odds_source"] for x in out} == {"predicted"}


def test_板を渡す引数がそもそも無い():
    """🔴 構造で塞ぐ。板を混ぜたくても混ぜられないことを固定する。"""
    import inspect

    from scripts.netkeirin_submit_wt import build_bet_lines
    for fn in (build_bet_detail, build_bet_lines):
        assert "odds" not in [
            n for n in inspect.signature(fn).parameters
            if n not in ("predicted_odds", "predicted_low")
        ], f"{fn.__name__} に板を渡せる引数が残っています"


def test_予測に無ければ不明のまま():
    """作れないものを作らない。null のままにして『未取得』と出させる。"""
    out = _lines(build_bet_detail(LEGS, "predicted", predicted_odds={}))
    assert all(x["odds"] is None for x in out)
    assert all(x["odds_source"] is None for x in out)


def test_三連単も予測盤面から書ける():
    """🔴 三連単のキーは tuple。ここが空だと 7H1 / 7T1 が板へ戻る
    （2026-08-26 まで実際にそうなっていた）。"""
    tf = [BetLeg(bet_kind="trifecta_formation", groups=[[1], [2], [3, 4]],
                 stake_per_line=1000)]
    out = _lines(build_bet_detail(
        tf, "equal", predicted_odds={(1, 2, 3): 45.6, (1, 2, 4): 78.9}))
    assert [x["odds"] for x in out] == [45.6, 78.9]
    assert {x["odds_source"] for x in out} == {"predicted"}


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
