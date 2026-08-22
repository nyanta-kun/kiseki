"""「厳選の二軸」の選定を固定する（2026-08-22）。

ユーザー判断: 的中率の高い側から、ガミになりそうなレースを除いて当日の上位3本。

🔴 **最初の案（期待値の上位3）は採らなかった。** EV が高い＝市場より強気＝人気薄で、
   実測では「厳選」が最も当たらない群になった（16.7% vs 全体 36.7%）。
   規則と実測は `src/premium_pick.py` の docstring。
"""
from __future__ import annotations

import re
from pathlib import Path

from src.premium_pick import (
    MIN_PAYOUT_RATIO,
    TOP_N,
    is_premium_candidate,
    select_premium,
)
from src.stake_allocation import MIN_POINT_ODDS

ROOT = Path(__file__).resolve().parent.parent


def _m(rk, p_hit, odds=3.0, ratio=1.2):
    return {"race_key": rk, "p_hit": p_hit,
            "min_point_odds": odds, "min_payout_ratio": ratio}


def test_的中率の高い順に選ぶ():
    """本数は `TOP_N` に依らず「多い順」であること（保留中は TOP_N=0）。"""
    got = select_premium([_m("c", 0.3), _m("a", 0.9), _m("b", 0.6), _m("d", 0.1)], top_n=3)
    assert got == ["a", "b", "c"]


def test_保留中は1本も選ばれない():
    """🔴 2026-08-22 夕からユーザー判断で停止中（`TOP_N = 0`）。

    実装は残してあるので、再開するときは 3 に戻すだけ。**戻す前に
    `keirin_handoff_2026_08_22_pm` の4つの課題を片付けること。**
    """
    assert TOP_N == 0, "保留を解除するなら、このテストの意図ごと更新すること"
    assert select_premium([_m("a", 0.9), _m("b", 0.6), _m("c", 0.3)]) == []


def test_安い目があるレースは厳選にしない():
    """A案と同じ 2.0 倍のゲート。"""
    assert select_premium([_m("a", 0.9, odds=MIN_POINT_ODDS - 0.01)], top_n=3) == []
    assert select_premium([_m("a", 0.9, odds=MIN_POINT_ODDS)], top_n=3) == ["a"]


def test_当たっても増えないレースは厳選にしない():
    """🔴 ユーザー要件「厳選のガミは許容できない」。判定は**下限包絡**で。"""
    assert select_premium([_m("a", 0.9, ratio=MIN_PAYOUT_RATIO - 0.01)], top_n=3) == []
    assert select_premium([_m("a", 0.9, ratio=MIN_PAYOUT_RATIO)], top_n=3) == ["a"]


def test_ガミ判定は下限包絡で測っている():
    """🔴 予測オッズで測ると実ガミが出る（初版がそうだった）。

    確定オッズは予測から大きく下振れする（買った点1,020点の実測で
    **40%が予測を割る**・下限包絡でも 22%）。`_premium_metrics` が
    `_conservative_trio_board` を通していることを構造で固定する。
    """
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    body = src.split("def _premium_metrics")[1].split("\ndef ")[0]
    assert "_conservative_trio_board" in body, \
        "厳選のガミ判定が下限包絡を通っていない（予測オッズのままだとガミが出る）"
    assert "expected_payout_floor(stakes, {k: v for k, v in low.items()" in body, \
        "想定払戻の算出に下限包絡を渡していない"


def test_測れないレースは特別扱いしない():
    """fail-closed。三連単ランクなど予測盤面が作れないものはここへ来る。"""
    assert is_premium_candidate({"p_hit": None, "min_point_odds": 3, "min_payout_ratio": 2}) is False
    assert is_premium_candidate({"p_hit": .9, "min_point_odds": None, "min_payout_ratio": 2}) is False
    assert is_premium_candidate({"p_hit": .9, "min_point_odds": 3, "min_payout_ratio": None}) is False


def test_同点でも並びが決まる():
    """🔴 揺れると、同じ日を2回処理したとき別のレースが『厳選』になる。"""
    a = select_premium([_m("b", 0.5), _m("a", 0.5), _m("c", 0.5)], top_n=2)
    b = select_premium([_m("c", 0.5), _m("b", 0.5), _m("a", 0.5)], top_n=2)
    assert a == b == ["a", "b"]


def test_候補が3本に満たなければそのまま():
    """⚠️ 4番手を繰り上げない（波ごとに違うレースが厳選になるため）。"""
    assert select_premium([_m("a", 0.9), _m("b", 0.5, odds=1.5)], top_n=3) == ["a"]
    assert select_premium([], top_n=3) == []


def test_ガミ許容度の既定():
    assert MIN_PAYOUT_RATIO == 1.0


def test_締めすぎない設計であること():
    """🔴 `MIN_PAYOUT_RATIO` を上げると的中が落ちて帰無と区別できなくなる。

    実測（8/16〜8/21・下限包絡）: 1.0 → 的中44.4%(ガミ0) / 1.1 → 33.3% / 1.2 → 27.8%。
    「安全側へ倒すほど良い」ではないので、値を上げる変更はここで落とす。
    """
    assert MIN_PAYOUT_RATIO <= 1.0, (
        "1.0 より上げると的中が落ちる（src/premium_pick.py の実測を参照）")


def test_入稿経路が選定を通している():
    """🔴 外れると例外もログも出ずに全レースが通常タイトルへ戻る。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert "select_premium(" in src, "入稿経路が選定を呼んでいない"
    assert "_PREMIUM_TITLE_TEMPLATE" in src, "厳選タイトルの差し替えが無い"
    assert re.search(r"premium_races and race_key in premium_races", src), \
        "差し替えの判定形が変わった"
    assert "厳選の二軸" in src


def test_厳選タイトルに増える系の語を入れない():
    """⚠️ 選ばれるのは当たりやすい3本で、実測は『2倍以上の的中』が0件。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if ln.startswith("_PREMIUM_TITLE_TEMPLATE"))
    for word in ("高配当", "妙味", "万車券", "大穴"):
        assert word not in line, f"厳選のタイトルに『{word}』を入れない"
