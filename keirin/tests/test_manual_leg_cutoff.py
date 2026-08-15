"""手動・看板穴埋め経路の相手足切りを固定する（2026-08-15）。

## なぜ必要になったか

相手の足切り（`相手の3着内率 >= 15%`）は `RANK_9C` の設計に入っているのに、
効いていたのは**候補JSON経由（ゲート通過）だけ**だった。手動・看板穴埋めは
`軸以外の全車`＝総流しで組んでおり、9車なら常に7点。

2026-08-15 松山6R（`1=7-2,5,9,3,6,4,8` の7点）をユーザーが見て
「下位3車は3指標いずれでも他車を逆転できず、相手として切れるはず」と指摘。
調べると当該レースは上位2車合計 1.210 < 1.30 で **9Cゲート不通過**＝穴埋めだった。
その日の 9C 入稿11件は全て `marquee_fill`。

🔴 **「ランクに足切りがある」＝「そのランクの入稿すべてに効く」ではない。**
   買い目の組み立ては経路ごとに別物なので、経路単位で固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import scripts.netkeirin_submit_wt as m
from src.strategy_wt import RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN


@pytest.fixture()
def probs(monkeypatch):
    """`_load_top3_probs` を差し替えるフィクスチャ。"""
    def _set(table: dict[int, float]):
        monkeypatch.setattr(m, "_load_top3_probs", lambda _rk: table)
    return _set


# 松山6R 2026-08-15 の実データ（pred_top3_pct / 0-1 スケール）。軸は 1-7。
MATSUYAMA_6R = {1: 0.670, 7: 0.540, 2: 0.465, 5: 0.338, 9: 0.309,
                3: 0.238, 6: 0.166, 4: 0.146, 8: 0.126}


def test_the_reported_race_drops_the_two_weakest(probs):
    """報告された実レースで、3着内率15%未満の 4・8 が落ちること。

    ⚠️ 車6（16.6%）は**残る**。ユーザーの見立て（4/6/8）とは1車ずれるが、
       6 まで落とすには閾値 0.18 以上が要る。**0.15 は 9C 本体と揃える判断**
       （2026-08-15 ユーザー決定）。0.20 のほうが表示的中は高いが取りこぼしが
       3.3%→6.8% に増える。
    """
    probs(MATSUYAMA_6R)
    got = m._manual_partners("20260815_75_06", "9C", 1, 7, 9)
    assert got == [2, 5, 9, 3, 6]


def test_ranks_without_a_cutoff_stay_on_the_full_spread(probs):
    """🔴 7A（7車の穴埋め）は総流しのまま。足切りは9車でしか測っていない。"""
    probs({c: 0.01 for c in range(1, 8)})
    assert m._manual_partners("20260815_75_06", "7A", 1, 2, 7) == [3, 4, 5, 6, 7]
    assert set(m.MANUAL_LEG_CUTOFF) == {"9C"}, \
        "足切り対象ランクを増やすときは walk-forward で測ってから"


def test_cutoff_threshold_follows_the_rank_definition():
    """閾値を手書きせず `RANK_9C_*` を参照していること（二重管理の防止）。"""
    assert m.MANUAL_LEG_CUTOFF["9C"] == (RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN)
    assert RANK_9C_LEG_P3_MIN == 0.15


def test_minimum_legs_are_restored_instead_of_skipping(probs):
    """🔴 足切りで最低点数を割っても**買わないにはできない**。

    看板レースには必ず推奨を出す方針（2026-08-09）で、この経路はその穴埋め。
    3着内率の上位から最低点数まで戻す。ゲート通過側は逆にレースごと落とすが、
    役割が違うので挙動が違うのは意図的。
    """
    probs({1: 0.60, 2: 0.55, 3: 0.14, 4: 0.13, 5: 0.12, 6: 0.11, 7: 0.10,
           8: 0.09, 9: 0.08})
    got = m._manual_partners("rk", "9C", 1, 2, 9)
    assert len(got) == RANK_9C_LEGS_MIN
    assert got == [3, 4, 5]          # 3着内率の高い順に戻す


def test_falls_back_to_the_full_spread_when_probabilities_are_missing(probs):
    """指数が読めないときは絞らない。**黙って点数を減らすほうが危険**
    （足切りは「当たらない相手を外す」施策で、外し過ぎは取りこぼしになる）。"""
    probs({})
    assert m._manual_partners("rk", "9C", 1, 2, 9) == [3, 4, 5, 6, 7, 8, 9]


def test_axes_are_never_included_as_partners(probs):
    probs(MATSUYAMA_6R)
    got = m._manual_partners("rk", "9C", 1, 7, 9)
    assert 1 not in got and 7 not in got


def test_partners_are_ordered_by_top3_rate(probs):
    """表示・配分の再現性のため降順で返す（`rank_7c_select_legs` と同じ）。"""
    probs(MATSUYAMA_6R)
    got = m._manual_partners("rk", "9C", 1, 7, 9)
    assert got == sorted(got, key=lambda c: -MATSUYAMA_6R[c])


# --- 経路の取り違え防止（静的検査）-----------------------------------------

def test_manual_path_does_not_build_partners_inline():
    """🔴 手動経路が `range(1, n_entries+1)` で相手を直接組み立てていないこと。

    ここを元に戻すと**足切りが黙って無効化される**（例外は出ず、点数が増えるだけ
    なので気づけない）。相手の決定は必ず `_manual_partners` を通す。
    """
    tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_manual_partners")
    inline = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node is fn:
            continue
        src = ast.unparse(node)
        if "range(1, n_entries + 1)" in src:
            inline.append(node.name)
    assert inline == [], f"相手を直接組み立てている関数がある: {inline}"
