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


def test_daily_select_accepts_the_deliberate_single_leg():
    """🔴 **1点は通す**（2026-08-24）。

    旧仕様は「1点まで落ちたら買わない」だったが、○1点への集中
    （`rank_7m1_maru_concentrates`）は**意図した1点買い**なので、ここで
    `RANK_7M1_LEGS_MIN`(=2) を要求すると集中したいレースが母集団ごと落ちる。
    下限2点は位置規則の戻し先であって選出のゲートではない。
    """
    assert sw.rank_7m1_daily_select([_cand(legs_7m1=[3])]) != []


def test_daily_select_rejects_empty_legs():
    """相手が1点も作れないレースは買わない。"""
    assert sw.rank_7m1_daily_select([_cand(legs_7m1=[])]) == []


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


# ── 堅い帯の取り込み（RANK_7M1_FIRM_BAND・2026-08-19） ──────────────────

def _firm(**kw):
    """堅い帯（7C の縄張り）で、7C が「軸1が抜けすぎ」で見送る候補。

    ◎が軸に入り○は入らない（`wt_overlap_7c_n == 1` かつ
    `wt_honmei_in_axis_7c is True`）状態を既定にする。
    """
    base = _cand(
        p3_sum_top2=sw.RANK_7M1_P3_SUM_MAX + 0.10,
        wt_overlap_7c_n=1,
        wt_honmei_in_axis_7c=True,
        legs_7c=[3, 4, 5, 6],
        legs_7c_buy=[3, 4, 5, 6],
        lowpay_pattern=False,
        axis1_p3=sw.RANK_7C_AXIS1_P3_MAX + 0.01,   # ← 7C はここで見送る
    )
    base.update(kw)
    return base


def test_firm_band_is_taken_when_7c_declines():
    """堅い帯でも、7C が見送る ∧ ◎あり・○なし なら 7M1 が拾う。"""
    c = _firm()
    assert not sw.rank_7c_accepts(c)
    assert sw.rank_7m1_takes_firm_band(c)
    assert sw.rank_7m1_daily_select([c]) == [c]


def test_firm_band_is_not_taken_when_7c_buys():
    """🔴 7C が買うレースは拾わない。

    入稿の優先順位は 7C > 7M1 なので、拾っても入稿されない紙の推奨が増えるだけ。
    ここを緩めると Web の 7M1 実績が「売っていない商品」で薄まる。
    """
    c = _firm(axis1_p3=sw.RANK_7C_AXIS1_P3_MAX - 0.01)   # 7C の見送り理由が消える
    assert sw.rank_7c_accepts(c)
    assert not sw.rank_7m1_takes_firm_band(c)
    assert sw.rank_7m1_daily_select([c]) == []


def test_firm_band_requires_honmei_in_axis():
    """🔴 ◎が軸に居ないレースは拾わない。

    ◎を軸から外した形は実測で**10倍以上の的中頻度が半減**する
    （7.6〜8.0% → 3.1〜4.2%）。配当中央は上がるが頻度の損失が上回る。
    """
    c = _firm(wt_honmei_in_axis_7c=False)
    assert not sw.rank_7m1_takes_firm_band(c)
    assert sw.rank_7m1_daily_select([c]) == []


def test_firm_band_is_fail_closed_without_the_mark_flag():
    """🔴 `wt_honmei_in_axis_7c` が無い古い候補JSONでは拾わない（fail-closed）。

    他ランクは情報欠損を fail-open にしているが、ここを fail-open にすると
    「◎が軸に居ない側」まで買ってしまい別の商品になる。
    """
    c = _firm()
    del c["wt_honmei_in_axis_7c"]
    assert not sw.rank_7m1_takes_firm_band(c)
    assert sw.rank_7m1_daily_select([c]) == []


def test_firm_band_requires_overlap_exactly_one():
    """◎○の両方が軸に入るレース（overlap==2）は従来どおり除外される。"""
    c = _firm(wt_overlap_7c_n=2)
    assert not sw.rank_7m1_takes_firm_band(c)
    assert sw.rank_7m1_daily_select([c]) == []


def test_firm_band_can_be_switched_off():
    """定数1つで元の挙動（混戦帯のみ）へ戻せること。"""
    c = _firm()
    assert not sw.rank_7m1_takes_firm_band(c, enabled=False)


def test_loose_band_is_unchanged_by_the_firm_band_work():
    """🔴 混戦帯の母集団は一切変えていないこと（回帰の要）。

    堅い帯の追加条件（◎あり・7C が見送る）を**混戦帯へ持ち込んではいけない**。
    """
    c = _cand(p3_sum_top2=sw.RANK_7M1_P3_SUM_MAX - 0.10, wt_overlap_7c_n=0)
    assert "wt_honmei_in_axis_7c" not in c
    assert sw.rank_7m1_daily_select([c]) == [c]


def test_rule_version_reacts_to_7c_constants():
    """🔴 7C の閾値を動かしたら 7M1 の版も動くこと。

    7M1 は `rank_7c_accepts` に依存するので、7C だけ変えて版が据え置かれると
    picks_history の世代が静かに混ざる（CLAUDE.md の rule_version の項）。
    """
    before = sw.rank_rule_version("7M1")
    orig = sw.RANK_7C_AXIS1_P3_MAX
    try:
        sw.RANK_7C_AXIS1_P3_MAX = orig - 0.05
        assert sw.rank_rule_version("7M1") != before
    finally:
        sw.RANK_7C_AXIS1_P3_MAX = orig
    assert sw.rank_rule_version("7M1") == before


def test_firm_band_switch_is_read_at_call_time(monkeypatch):
    """🔴 定数を書き換えたら**その場で**効くこと（既定引数に束縛しない）。

    `def f(..., enabled=RANK_7M1_FIRM_BAND)` と書くと既定値は定義時に確定し、
    定数を差し替えても切り替わらない。検証で ON/OFF を比較したときに
    「両方 ON」のまま走って増分0件と誤読する事故が実際に起きた。
    """
    c = _firm()
    assert sw.rank_7m1_takes_firm_band(c)
    monkeypatch.setattr(sw, "RANK_7M1_FIRM_BAND", False)
    assert not sw.rank_7m1_takes_firm_band(c)
    assert sw.rank_7m1_daily_select([c]) == []


def test_firm_band_is_fail_closed_without_the_7c_judgment_keys():
    """🔴 7C の受理判定に要るキーが欠けた候補では拾わない（fail-closed）。

    `rank_7c_accepts` は旧候補JSON向けに一部を fail-open で扱うため、
    キーを渡し忘れると「7Cは買わない」と誤判定され、**7C が実際に買う
    レースまで 7M1 が拾う**（実測で母集団が19%膨らむ）。
    """
    for key in sw._FIRM_BAND_REQUIRED_KEYS:
        c = _firm()
        del c[key]
        assert not sw.rank_7m1_takes_firm_band(c), f"{key} が欠けても拾ってしまう"
        assert sw.rank_7m1_daily_select([c]) == []


# ── 相手を EV（予測オッズ × 3着内確率）順にする（2026-08-21・ユーザー提案）──
#
# 検証: 3点据え置きで「2倍以上で的中」が +0.30 / +0.22 件/日（両窓とも有意）。
# memory: keirin_7m1_ev_legs_2026_08_21
#
# 🔴 **2026-08-26 から EV 経路は休止中**（`RANK_7M1_LEG_ORDER = "position"`）。
#    以下は `order="ev"` を明示して経路そのものを守るテスト。"ev" へ戻したときに
#    2026-08-21/24 の検証どおり動くことを保証する。休止の理由は
#    `strategy_wt.RANK_7M1_MARK_DEMOTE` 定義部の「2026-08-26」節。

def test_select_legs_uses_ev_order_when_available():
    """EV が全候補に揃っていれば **EV の降順で上位 `RANK_7M1_LEGS` 点**を採る。

    点数は 2026-08-24 に 3 → 4。均等配分で測った旧検証が「増点は悪化」と
    出していたのは配分の取り違えで、本番のダッチングで測り直すと4点が優る
    （`strategy_wt.RANK_7M1_MARK_DEMOTE` 定義部）。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.10}
    ev = {1: 0.9, 2: 1.5, 3: 0.8, 4: 2.2, 5: 1.1}
    assert sw.RANK_7M1_LEGS == 4
    assert sw.rank_7m1_select_legs(others, p3, ev=ev, order="ev") == [4, 2, 5, 1]


def test_select_legs_ev_does_not_apply_the_p3_floor():
    """🔴 EV 順では足切り（`p3_min`）を掛けない。

    足切りは「下位3車を位置で採る」規則の副作用を均すもので、EV は既に
    オッズ×確率で釣り合っている。ここで p3 の低い車を削ると、EV が狙う
    「市場が安く付けているのに来る」相手を落として元の低配当側へ寄る。
    **検証も足切り無しの形で行ったので、足すと検証と別物になる。**
    """
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.02, 5: 0.01}   # 4,5 は足切り水準以下
    ev = {1: 0.1, 2: 0.2, 3: 0.3, 4: 9.0, 5: 8.0}
    assert sw.rank_7m1_select_legs(others, p3, ev=ev, order="ev") == [4, 5, 3, 2]


def test_select_legs_falls_back_when_ev_is_missing_or_partial():
    """EV が None / 1台でも欠けたら従来規則へ落ちる。

    🔴 一部だけ EV を使うと EV 順と指数順が混ざり、並びの意味が壊れる。
       予測オッズは 7車・9車以外（実測3.7%）で作れないので、この経路は必ず通る。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.16}
    base = sw.rank_7m1_select_legs(others, p3)
    assert sw.rank_7m1_select_legs(others, p3, ev=None, order="ev") == base
    assert sw.rank_7m1_select_legs(
        others, p3, ev={1: 1.0, 2: 2.0}, order="ev") == base


def test_leg_order_constant_is_the_actual_switch():
    """🔴 **`RANK_7M1_LEG_ORDER` が実際の分岐であること**（2026-08-26 に配線）。

    以前は「呼び出し元が `ev=` を渡すかどうか」が分岐で、この定数は
    `rank_rule_version` にしか効かない**飾りのスイッチ**だった。呼び出し元は
    live（`cli/main.py`）と日次 tail 再構築（`backfill_7m1_rank_wt.py`）の2つ
    あり、定数だけを戻した人は「戻したつもりで挙動が変わらない」ことに
    気付けない（例外もログも出ない）。

    ここでは **"position" のときに ev/odds/marks を渡しても無視される**ことを
    固定する。○1点集中（`RANK_7M1_MARU_CONC_*`）が休止していることも同時に守る。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.16}
    ev = {1: 9.0, 2: 8.0, 3: 3.0, 4: 2.0, 5: 1.0}
    marks = {1: 2, 2: 0, 3: 0, 4: 0, 5: 0}
    odds = {1: 2.5, 2: 30.0, 3: 20.0, 4: 15.0, 5: 10.0}   # ○が集中帯のど真ん中

    assert sw.RANK_7M1_LEG_ORDER == "position"
    position_only = sw.rank_7m1_select_legs(others, p3)
    assert sw.rank_7m1_select_legs(
        others, p3, ev=ev, odds=odds, marks=marks) == position_only
    # 帯の中でも1点買いにならない（○集中は EV 経路の中にある）
    assert len(position_only) >= sw.RANK_7M1_LEGS_MIN
    # "ev" を明示したときだけ集中する
    assert sw.rank_7m1_select_legs(
        others, p3, ev=ev, odds=odds, marks=marks, order="ev") == [1]


def test_leg_order_constant_moves_the_rule_version():
    """`RANK_7M1_LEG_ORDER` は版に効く定数であること。

    値を戻したときに `picks_history.rule_version` が旧世代と同じにならないと、
    集計で新旧が混ざる。
    """
    st = sw

    assert st.RANK_7M1_LEG_ORDER == "position"
    before = st.rank_rule_version("7M1")
    st.RANK_7M1_LEG_ORDER = "ev"
    try:
        assert st.rank_rule_version("7M1") != before
    finally:
        st.RANK_7M1_LEG_ORDER = "position"


def test_daily_rebuild_passes_ev_to_select_legs():
    """🔴 **日次 tail 再構築も live と同じ材料を渡していること。**

    live だけ EV にすると、毎朝の `reconcile_walkforward_tail.sh` が当月を
    作り直すたびに picks_history が別規則へ**巻き戻る**。
    7C が 2026-08-15 に実際に踏んだ型で、そのときは入稿と記録が
    84件中17件で食い違う実害になった。

    ⚠️ 2026-08-26 以降、実際にどちらの規則で並べるかは
    `RANK_7M1_LEG_ORDER`（現行 "position"）が決めるので、両側が渡していても
    買い目は割れない。それでも**渡す材料を揃えておく**のは、"ev" へ戻した
    瞬間に再構築側だけ旧規則へ落ちるのを防ぐため。
    """
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "backfill_7m1_rank_wt.py").read_text()
    assert "rank_7m1_select_legs(" in src
    assert "_ev_for(" in src, "再構築側が EV を渡していない（巻き戻りが起きる）"
    # 🔴 2026-08-24: 相手選択は EV だけでなく **予測オッズと印**も見る。
    #    どれか1つでも渡し忘れると、live と再構築で別の買い目になり
    #    picks_history が入稿と食い違う（7C が 2026-08-15 に踏んだ型）。
    assert "odds=" in src, "再構築側が予測オッズを渡していない（○集中が効かない）"
    assert "marks=" in src, "再構築側が印を渡していない（○△の後回しが効かない）"


def test_backfill_ev_is_disabled_before_the_odds_model_train_end():
    """オッズモデルの学習終端以前の日付では EV を使わない（in-sample 防止）。"""
    import scripts.backfill_7m1_rank_wt as bf

    bf._EV_WARNED = False
    assert bf._ev_for("20260101_11_01", 1, 2, [3, 4, 5], "2020-01-01") is None
    assert bf._ev_for("20260101_11_01", 1, 2, [3, 4, 5], "") is None


def test_backfill_ev_never_raises_when_the_odds_model_is_missing():
    """🔴 モデル未配備でも **例外にせず従来規則へ落ちる**こと。

    `odds_prediction.model_train_end()` はメタが無いと例外を投げる。
    `keirin/data` はリポジトリ管理外なので、モデルの無い環境では必ずここを通る。
    素通しにすると **tail 再構築ごと落ちて当日の行が消える**。
    2026-08-21 に CI で実際に落ちて発覚した（ローカルはモデルがあるので通っていた）。
    """
    import scripts.backfill_7m1_rank_wt as bf

    def boom():
        raise RuntimeError("odds_trio_meta.json がありません")

    orig = bf.odds_model_train_end
    bf.odds_model_train_end = boom
    bf._EV_WARNED = False
    try:
        assert bf._ev_for("20260101_11_01", 1, 2, [3, 4, 5], "2099-01-01") is None
    finally:
        bf.odds_model_train_end = orig


# ── 相手の印による後回し / ○1点への集中（2026-08-24） ─────────────────
#
# 根拠と実測は `strategy_wt.RANK_7M1_MARK_DEMOTE` 定義部のセクションコメント。
# memory: keirin_7m1_partner_count_2026_08_24

def test_select_legs_demotes_marked_partners():
    """○(mark2)/△(mark4) は EV が高くても**相手の後ろへ回す**。

    「軸2＋その車」を1点買いした素ROI は ○ 68〜70% / △ 71〜74% と控除率の壁
    (74.85%) の下で、無印 77〜86% に負ける。しかもダッチングは人気の相手ほど
    厚く置くので、放っておくと**賭け金の過半が負ける側に乗る**（実測 55.9%）。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {c: 0.3 for c in others}
    ev = {1: 9.0, 2: 8.0, 3: 3.0, 4: 2.0, 5: 1.0}
    marks = {1: 2, 2: 4, 3: 0, 4: 0, 5: 0}      # 1=○ 2=△ 残りは無印
    # 無印(3,4,5)が EV 順で先、そのあと ○△ が EV 順で続く → 4点目は○
    assert sw.rank_7m1_select_legs(
        others, p3, ev=ev, marks=marks, order="ev") == [3, 4, 5, 1]


def test_select_legs_without_marks_keeps_the_old_ev_order():
    """`marks` を渡さない呼び出しは**後回しも集中もしない**（fail-open）。

    印が取れない日に静かに別の商品へ変わらないこと。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {c: 0.3 for c in others}
    ev = {1: 9.0, 2: 8.0, 3: 3.0, 4: 2.0, 5: 1.0}
    assert sw.rank_7m1_select_legs(others, p3, ev=ev, order="ev") == [1, 2, 3, 4]


def test_maru_concentration_band():
    """○を含む点の**予測**オッズが 2.0〜3.1倍 のときだけ ○1点へ集中する。"""
    assert sw.rank_7m1_maru_concentrates(3, {3: 2.5}) is True
    assert sw.rank_7m1_maru_concentrates(3, {3: 2.0}) is True     # 下限は含む
    assert sw.rank_7m1_maru_concentrates(3, {3: 3.1}) is True     # 上限は含む
    assert sw.rank_7m1_maru_concentrates(3, {3: 1.9}) is False    # 安すぎる
    assert sw.rank_7m1_maru_concentrates(3, {3: 3.2}) is False    # 人気が集まっていない


def test_maru_concentration_is_fail_closed():
    """○が居ない / 予測オッズが無いときは集中しない（根拠なく1点買いにしない）。"""
    assert sw.rank_7m1_maru_concentrates(None, {3: 2.5}) is False
    assert sw.rank_7m1_maru_concentrates(3, None) is False
    assert sw.rank_7m1_maru_concentrates(3, {}) is False
    assert sw.rank_7m1_maru_concentrates(3, {4: 2.5}) is False    # ○の点が無い


def test_maru_concentration_lower_bound_tracks_min_point_odds():
    """🔴 下限は入稿側の `MIN_POINT_ODDS` と**必ず同じ値**であること。

    入稿は「2倍未満の目が1つでもあるレースは出さない」ので、下限を下げると
    ○1点へ絞った瞬間に**そのレースが丸ごと落ちる**（実測で発火の27〜30%）。
    下限を揃えておけば、その帯は集中せず通常の相手構成のまま売れる。
    """
    from src.stake_allocation import MIN_POINT_ODDS

    assert sw.RANK_7M1_MARU_CONC_ODDS_MIN == MIN_POINT_ODDS


def test_maru_concentration_thresholds_are_not_bound_at_import_time():
    """🔴 帯の既定値は**呼び出し時**に読むこと。

    `def f(lo=RANK_7M1_MARU_CONC_ODDS_MIN)` と書くと定義時に確定し、定数を
    書き換えても切り替わらない「効かないスイッチ」になる
    （`rank_7m1_takes_firm_band` で実際に踏んだ型）。
    """
    orig = sw.RANK_7M1_MARU_CONC_ODDS_MAX
    sw.RANK_7M1_MARU_CONC_ODDS_MAX = 10.0
    try:
        assert sw.rank_7m1_maru_concentrates(3, {3: 5.0}) is True
    finally:
        sw.RANK_7M1_MARU_CONC_ODDS_MAX = orig
    assert sw.rank_7m1_maru_concentrates(3, {3: 5.0}) is False


def test_select_legs_concentrates_on_maru_before_demoting_it():
    """🔴 集中判定は**後回しより先**。

    順序を入れ替えると ○ が後ろへ回された結果、集中したいレースで
    ○ を買っていないという矛盾が起きる。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {c: 0.3 for c in others}
    ev = {1: 0.1, 2: 8.0, 3: 3.0, 4: 2.0, 5: 1.0}
    marks = {1: 2, 2: 0, 3: 0, 4: 0, 5: 0}       # 1 が○（EV は最下位）
    odds = {1: 2.5, 2: 30.0, 3: 20.0, 4: 15.0, 5: 10.0}
    assert sw.rank_7m1_select_legs(
        others, p3, ev=ev, odds=odds, marks=marks, order="ev") == [1]


def test_select_legs_falls_back_to_four_points_outside_the_band():
    """帯の外なら集中せず、○を後回しにした4点になる。"""
    others = [1, 2, 3, 4, 5]
    p3 = {c: 0.3 for c in others}
    ev = {1: 9.0, 2: 8.0, 3: 3.0, 4: 2.0, 5: 1.0}
    marks = {1: 2, 2: 0, 3: 0, 4: 0, 5: 0}
    odds = {1: 1.5, 2: 30.0, 3: 20.0, 4: 15.0, 5: 10.0}   # ○が安すぎる
    # ○(1) は EV 最上位でも後ろへ回るので、無印4車がそのまま4点になる
    assert sw.rank_7m1_select_legs(
        others, p3, ev=ev, odds=odds, marks=marks, order="ev") == [2, 3, 4, 5]


def test_concentration_never_fires_without_ev():
    """予測オッズが作れない（EV も無い）レースでは従来の位置規則のまま。"""
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.16}
    marks = {1: 2, 2: 0, 3: 0, 4: 0, 5: 0}
    odds = {1: 2.5, 2: 30.0, 3: 20.0, 4: 15.0, 5: 10.0}
    assert (sw.rank_7m1_select_legs(others, p3, odds=odds, marks=marks, order="ev")
            == sw.rank_7m1_select_legs(others, p3))


def test_new_constants_move_the_rule_version():
    """🔴 新しい定数は `picks_history.rule_version` に効くこと。

    効かないと、改定の前後が同じ世代として混ざって集計できない。
    タプルではなく文字列で持っているのは `rank_rule_version` が
    スカラしか拾わないため（`RANK_7M1_MARK_DEMOTE`）。
    """
    for name, alt in (("RANK_7M1_MARK_DEMOTE", "4"),
                      ("RANK_7M1_MARU_CONC_ODDS_MIN", 2.5),
                      ("RANK_7M1_MARU_CONC_ODDS_MAX", 4.0),
                      ("RANK_7M1_LEGS", 3)):
        orig = getattr(sw, name)
        before = sw.rank_rule_version("7M1")
        setattr(sw, name, alt)
        try:
            assert sw.rank_rule_version("7M1") != before, f"{name} が版に効いていない"
        finally:
            setattr(sw, name, orig)
        assert sw.rank_rule_version("7M1") == before


def test_demote_marks_parses_the_constant():
    """`RANK_7M1_MARK_DEMOTE` は「後ろへ回す印」のカンマ区切り。空なら後回ししない。"""
    assert sw.rank_7m1_demoted_marks() == {2, 4}
    orig = sw.RANK_7M1_MARK_DEMOTE
    sw.RANK_7M1_MARK_DEMOTE = ""
    try:
        assert sw.rank_7m1_demoted_marks() == set()
        others = [1, 2, 3, 4, 5]
        p3 = {c: 0.3 for c in others}
        ev = {1: 9.0, 2: 8.0, 3: 3.0, 4: 2.0, 5: 1.0}
        marks = {1: 2, 2: 4, 3: 0, 4: 0, 5: 0}
        assert sw.rank_7m1_select_legs(
            others, p3, ev=ev, marks=marks, order="ev") == [1, 2, 3, 4]
    finally:
        sw.RANK_7M1_MARK_DEMOTE = orig


def test_live_passes_odds_and_marks_to_select_legs():
    """🔴 live 側（`cli/main.py`）も予測オッズと印を渡していること。

    再構築側だけに入れると、入稿と記録が食い違う（逆向きの巻き戻り）。
    盤面計算は1回にまとめる（`trio_ev_and_odds_for_legs`）。
    """
    src = (Path(__file__).resolve().parent.parent
           / "src" / "cli" / "main.py").read_text()
    assert "trio_ev_and_odds_for_legs" in src
    i = src.index("rank_7m1_select_legs(")
    call = src[i:i + 400]
    assert "odds=" in call and "marks=" in call


def test_backfill_uses_mark3_for_the_7c_ana_cut():
    """🔴 再構築の `wt_ana` は **mark3**（live と揃えること）。

    live（`cli/main.py`）は `prediction_mark == 3` を候補JSON の `wt_ana` に載せ、
    `backfill_7c_rank_wt` もそれを使う。ここだけ mark4 を渡していた時期があり、
    `rank_7c_drop_ana_leg` の発動が live と食い違って **51% のレースで
    `legs_7c` が変わっていた**（`legs_7c_buy` は 7M1 の堅い帯ゲートの入力）。

    ⚠️ 変数名の「ana(穴)」に引きずられないこと。`wt_ana` は名前に反して ▲ で、
       最弱の印は mark4(△)。7B の相手除外印も再検証で mark3 が最良と確定している。
    """
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "backfill_7m1_rank_wt.py").read_text()
    i = src.index("rank_7c_buy_plan(")
    call = src[i:i + 200]
    assert "wt_ana=mk.get(3)" in call, "再構築の wt_ana が live(mark3) と違う"


def test_rebuild_keeps_the_deliberate_single_point():
    """🔴 再構築も**1点を捨てない**こと（2026-08-24）。

    `backfill_7m1_rank_wt` は `len(combos) < RANK_7M1_LEGS_MIN`(=2) で弾いており、
    ○1点への集中が丸ごと落ちていた（実測で 2026-08 の再構築 212件中 集中 0件）。
    `judge_rank_7m1` と同じく「**買い目の全点にオッズがあること**」を要求する。

    この3箇所（live / 発走前の記録 / 再構築）はどれか1つでも取り残すと
    入稿と記録が食い違う。PR#289 と同じ型なので構造で塞ぐ。
    """
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "backfill_7m1_rank_wt.py").read_text()
    assert "< RANK_7M1_LEGS_MIN" not in src, "再構築が最低点数で弾いている（1点が消える）"
    assert 'len(combos) < len(c_["legs_7m1"])' in src
