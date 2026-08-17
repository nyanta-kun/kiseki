"""RANK_7M1（中間層・混戦 × 市場乖離）の回帰テスト（2026-08-17 新設）。

このランクは「7C の裏返し」で定義されているので、**7C 側を動かしたときに
隙間や重複が生まれないこと**が最重要の不変条件になる。加えて、設計の要である

  - 相手は「軸を除く5車のうち下位3車」＝全体では指数5〜7番手（**選抜順が先**）
  - 足切り（p3>=0.15）は**その後で削るだけ**。5車全体からの選抜に使うと帯が消える
  - 公式印が取れないレースは **買わない**（fail-closed）

の2点は、うっかり他ランクの流儀（足切り・fail-open）へ寄せると静かに別の
商品になるため、ここで固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.strategy_wt as sw


def _cand(**kw):
    """7M1 の選出に必要な最低限のキーを持つ候補を作る。"""
    base = {
        "n_entries": 7,
        "p3_sum_top2": 1.20,
        "wt_overlap_7c_n": 0,
        "legs_7m1": [3, 4, 5],
    }
    base.update(kw)
    return base


# ── 相手の取り方 ──────────────────────────────────────────────────────

def test_select_legs_takes_third_to_fifth():
    """相手は「軸2車を除く5車」の3〜5番目＝レース全体では指数5〜7番手の3点。

    🔴 「モデル3〜5番手」ではない。数えているのは**相手の中での順位**で、
       全体順位ではない（説明文でここを取り違えると別の商品を説明することになる）。
    """
    # 位置の検査なので、足切り（p3>=0.15）は全車が通る値にしてある。
    probs = {1: 0.90, 2: 0.80, 3: 0.50, 4: 0.40, 5: 0.30, 6: 0.25, 7: 0.20}
    others = [3, 4, 5, 6, 7]          # 軸(1,2)を除いた5車
    assert sw.rank_7m1_select_legs(others, probs) == [5, 6, 7]


def test_select_legs_applies_floor_after_taking_the_bottom_three():
    """足切りは「下位3車を採った後」に掛ける（2026-08-17 追加）。"""
    # 下位3車 = 5,6,7。うち 7 だけが 0.15 未満なので削られて2点になる。
    probs = {1: 0.90, 2: 0.80, 3: 0.50, 4: 0.40, 5: 0.30, 6: 0.20, 7: 0.10}
    assert sw.rank_7m1_select_legs([3, 4, 5, 6, 7], probs) == [5, 6]


def test_select_legs_floor_never_promotes_the_top_opponents():
    """🔴 足切りで点数が落ちても、**相手の上位2枚（全体3・4番手）は買わない**。

    ここを補充で埋めると 7C/7S が既に取っている低配当の目を買うことになり、
    この層の存在意義が消える。戻すのは下位3車の中の上位側までに限る。
    """
    # 下位3車(5,6,7)がすべて 0.15 未満 → 最低2点まで戻すが 3,4 は入らない。
    probs = {1: 0.90, 2: 0.80, 3: 0.50, 4: 0.40, 5: 0.14, 6: 0.10, 7: 0.05}
    legs = sw.rank_7m1_select_legs([3, 4, 5, 6, 7], probs)
    assert legs == [5, 6]
    assert 3 not in legs and 4 not in legs


def test_select_legs_floor_is_not_a_selection_rule():
    """🔴 7C の「5車全体から p3>=0.15 を選ぶ」規則にしていないこと。

    もし選抜に使っていたら、上位の 3,4 が残って [3,4,5] を返してしまう。
    """
    probs = {1: 0.90, 2: 0.80, 3: 0.50, 4: 0.40, 5: 0.30, 6: 0.20, 7: 0.16}
    legs = sw.rank_7m1_select_legs([3, 4, 5, 6, 7], probs)
    assert legs == [5, 6, 7]
    assert sw.rank_7c_select_legs([3, 4, 5, 6, 7], probs) == [3, 4, 5, 6, 7]


def test_floor_constant_is_shared_with_7c():
    """値は 7C と同じ定数を共有する（新しいマジックナンバーを増やさない）。"""
    assert sw.RANK_7M1_LEG_P3_MIN == sw.RANK_7C_LEG_P3_MIN


def test_select_legs_short_field_returns_fewer():
    """欠車で相手が足りないときは埋め合わせず、少ないまま返す
    （買うかどうかは呼び出し側の点数チェックで決める）。"""
    probs = {1: 0.9, 2: 0.8, 3: 0.5, 4: 0.4}
    assert sw.rank_7m1_select_legs([3, 4], probs) == []


# ── 選出ゲート ────────────────────────────────────────────────────────

def test_daily_select_accepts_konsen_and_disagreement():
    got = sw.rank_7m1_daily_select([_cand()])
    assert len(got) == 1


def test_daily_select_rejects_when_7c_takes_it():
    """合計が 7C の下限以上なら 7M1 は取らない（両ランクは排他）。"""
    c = _cand(p3_sum_top2=sw.RANK_7C_P3_SUM_MIN)
    assert sw.rank_7m1_daily_select([c]) == []


def test_gate_is_exactly_the_complement_of_7c():
    """🔴 7C と 7M1 の合計ゲートは**同じ定数を共有**し、隙間も重複も無いこと。

    別々の定数に分かれると、片方を動かしたときに
    「どちらも取らない帯」または「両方が取る帯」が静かに生まれる。
    """
    assert sw.RANK_7M1_P3_SUM_MAX == sw.RANK_7C_P3_SUM_MIN


def test_daily_select_rejects_mark_agreement():
    """公式印 ◎○ と軸2車が一致するレースは対象外（overlap==2）。"""
    assert sw.rank_7m1_daily_select([_cand(wt_overlap_7c_n=2)]) == []


def test_daily_select_accepts_partial_mark_overlap():
    """片方だけ重なる（overlap==1）は「不一致」として扱う。"""
    assert len(sw.rank_7m1_daily_select([_cand(wt_overlap_7c_n=1)])) == 1


def test_daily_select_is_fail_closed_without_marks():
    """🔴 印が取れないレースは**買わない**。

    他ランクは情報欠損を fail-open（買う）にしているが、7M1 は
    「印と割れていること」がエッジの本体なので、確認できない以上は降りる。
    fail-open にすると印の取得が壊れた日だけ母集団が膨らんで別物になる。
    """
    assert sw.rank_7m1_daily_select([_cand(wt_overlap_7c_n=None)]) == []


def test_daily_select_accepts_two_legs_after_floor():
    """足切りで2点になったレースは買う（正常な結果）。"""
    assert len(sw.rank_7m1_daily_select([_cand(legs_7m1=[3, 4])])) == 1


def test_daily_select_rejects_single_leg():
    """1点まで落ちたら買わない（1点買いは商品として説明できない・7C と同じ判断）。"""
    assert sw.rank_7m1_daily_select([_cand(legs_7m1=[3])]) == []


def test_daily_select_rejects_non_seven_car():
    assert sw.rank_7m1_daily_select([_cand(n_entries=9)]) == []


def test_daily_select_uses_calibrated_sum_when_present():
    """ゲートは較正後の値を優先する（7C と同じ `_gate_p3_sum` を通す）。"""
    # 生は通らない値でも、較正後が閾値未満なら対象になる。
    c = _cand(p3_sum_top2=1.50, p3_sum_top2_cal=1.40)
    assert len(sw.rank_7m1_daily_select([c])) == 1
    # 逆に較正後が閾値以上なら落ちる。
    c2 = _cand(p3_sum_top2=1.40, p3_sum_top2_cal=1.50)
    assert sw.rank_7m1_daily_select([c2]) == []


def test_daily_select_sorts_by_confidence_desc():
    a = _cand(p3_sum_top2=1.10, legs_7m1=[3, 4, 5])
    b = _cand(p3_sum_top2=1.35, legs_7m1=[3, 4, 5])
    got = sw.rank_7m1_daily_select([a, b])
    assert [c["p3_sum_top2"] for c in got] == [1.35, 1.10]


# ── 単一正本への登録 ──────────────────────────────────────────────────

def test_registered_in_current_paper_ranks():
    specs = {s.rank: s for s in sw.CURRENT_PAPER_RANKS}
    assert "RANK_7M1" in specs
    spec = specs["RANK_7M1"]
    assert spec.suffix == "#7M1"
    assert spec.label == "7M1"
    # ベース層とは別集計（ヘッダー合計に混ぜない）。
    assert spec.in_header_total is False
    assert spec.in_live_report is True


def test_submission_priority_is_last():
    """🔴 入稿の優先順位は最下位（ユーザー指示 2026-08-17「7H1 の下」）。

    7S とは当たり方が部分集合、7H1 とは排他だが ROI で劣るため、
    重なったら必ず譲る。順序を上げると既存ランクの母集団を削る。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from scripts.netkeirin_submit_wt import RANK_ORDER
    assert RANK_ORDER[-1] == "7M1"
