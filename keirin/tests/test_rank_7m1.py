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

def test_select_legs_uses_ev_order_when_available():
    """EV が全候補に揃っていれば **EV の降順で上位3点**を採る。"""
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.10}
    ev = {1: 0.9, 2: 1.5, 3: 0.8, 4: 2.2, 5: 1.1}
    assert sw.rank_7m1_select_legs(others, p3, ev=ev) == [4, 2, 5]


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
    assert sw.rank_7m1_select_legs(others, p3, ev=ev) == [4, 5, 3]


def test_select_legs_falls_back_when_ev_is_missing_or_partial():
    """EV が None / 1台でも欠けたら従来規則へ落ちる。

    🔴 一部だけ EV を使うと EV 順と指数順が混ざり、並びの意味が壊れる。
       予測オッズは 7車・9車以外（実測3.7%）で作れないので、この経路は必ず通る。
    """
    others = [1, 2, 3, 4, 5]
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.16}
    base = sw.rank_7m1_select_legs(others, p3)
    assert sw.rank_7m1_select_legs(others, p3, ev=None) == base
    assert sw.rank_7m1_select_legs(others, p3, ev={1: 1.0, 2: 2.0}) == base


def test_leg_order_constant_moves_the_rule_version():
    """`RANK_7M1_LEG_ORDER` は版に効く定数であること。

    値を戻したときに `picks_history.rule_version` が旧世代と同じにならないと、
    集計で新旧が混ざる。
    """
    st = sw

    assert st.RANK_7M1_LEG_ORDER == "ev"
    before = st.rank_rule_version("7M1")
    st.RANK_7M1_LEG_ORDER = "position"
    try:
        assert st.rank_rule_version("7M1") != before
    finally:
        st.RANK_7M1_LEG_ORDER = "ev"


def test_daily_rebuild_passes_ev_to_select_legs():
    """🔴 **日次 tail 再構築も EV を渡していること。**

    live だけ EV にすると、毎朝の `reconcile_walkforward_tail.sh` が当月を
    作り直すたびに picks_history が旧規則（下位3車）へ**巻き戻る**。
    7C が 2026-08-15 に実際に踏んだ型で、そのときは入稿と記録が
    84件中17件で食い違う実害になった。
    """
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "backfill_7m1_rank_wt.py").read_text()
    assert "rank_7m1_select_legs(" in src
    assert "ev=_ev_for(" in src, "再構築側が EV を渡していない（巻き戻りが起きる）"


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
