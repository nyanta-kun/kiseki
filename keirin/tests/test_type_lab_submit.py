"""型ラボの**入稿**（`scripts/netkeirin_submit_type_lab.py`）の検査。

固定したいのは3つ。どれも壊れても例外が出ない種類の事故なので構造で守る。

1. **1レース1商品**。型は排他なので `sell_plans_for` は必ず 0 個か 1 個。
   2つ返るようになったら優先順位の設計が別途要る＝設計変更の合図。
2. **賭け金を作り直さない**。20か月測ったものと売るものが別になる事故は
   `CLAUDE.md` の「検証の作法」が繰り返し記録している型。
3. **ゲートの正本を写経しない**。軸信頼ゲートは backend 側のファイルを
   読み込んで束縛する（閾値がプランごとに8つあり、片方だけ更新されると
   画面と入稿が静かに食い違う）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src.type_lab import PLANS, SELL_PLANS, sell_plans_for

REPO = Path(__file__).resolve().parent.parent
SUBMIT_PY = REPO / "scripts" / "netkeirin_submit_type_lab.py"


# ───────────────────────── 1レース1商品 ─────────────────────────

@pytest.mark.parametrize("type_label", list("ABCDEF"))
@pytest.mark.parametrize("n_entries", [7, 9])
@pytest.mark.parametrize("race_type", ["決勝", "準決勝", "予選", "特選", None])
def test_sell_plans_is_at_most_one(type_label, n_entries, race_type):
    """売るプランは型ごとにちょうど1つ（か0）。ここが2になると設計が変わる。"""
    got = sell_plans_for(type_label, n_entries, race_type)
    assert len(got) <= 1, f"{type_label}/{n_entries}車/{race_type} → {[p.key for p in got]}"


def test_every_type_has_a_plan_for_7car():
    """7車は全6型に売るものがある（見送りの型を作らない）。"""
    for t in "ABCDEF":
        assert len(sell_plans_for(t, 7)) == 1, t


def test_sell_plans_matches_constant():
    """`SELL_PLANS` は A〜E が hit・F が pay（2026-08-28 のユーザー決定）。"""
    assert SELL_PLANS == ("A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_pay")
    assert set(SELL_PLANS) <= set(PLANS), "SELL_PLANS に PLANS 外のキーがある"


def test_nine_car_type_f_splits_by_race_type():
    """9車の型F は**決勝は F_pay・それ以外は F_hit**（2026-08-30 ユーザー判断）。

    🔴 **決勝以外を「売らない」に戻さないこと。** 2026-08-30 に 9車開催の
       看板8件（選抜3・特選3・特秀2）が無商品になり、旧ランクの看板穴埋めも
       型ラボ全面移行で機能しなくなっていた（埋まらなかった看板 8/29 15件 →
       8/30 21件）。「看板には必ず出す」方針を優先した選択。
    🔴 全部 `F_pay` で売ると表示的中 6.07%・ROI 60.5% で壁の下。だから
       決勝以外は当たる回数を売る `F_hit` に替えてある。
    """
    assert [p.key for p in sell_plans_for("F", 9, "決勝")] == ["F_pay"]
    assert [p.key for p in sell_plans_for("F", 9, "準決勝")] == ["F_hit"]
    assert [p.key for p in sell_plans_for("F", 9, "選抜")] == ["F_hit"]
    assert [p.key for p in sell_plans_for("F", 9, None)] == ["F_hit"]
    # 7車は変えていない
    assert [p.key for p in sell_plans_for("F", 7, "決勝")] == ["F_pay"]
    assert [p.key for p in sell_plans_for("F", 7, "選抜")] == ["F_pay"]
    # 型F以外は9車でも種別によらず売る
    assert [p.key for p in sell_plans_for("A", 9, "特選")] == ["A_hit"]


def test_every_type_has_a_plan_for_9car():
    """🔴 9車も全6型に売るものがある（**見送りの型を作らない**）。

    ここが 0 に戻ったら、その型のレースは商品ゼロになる。看板が含まれていれば
    「看板には必ず出す」方針に反する（2026-08-30 に実際に起きた）。
    """
    for t in "ABCDEF":
        for rt in ("決勝", "準決勝", "選抜", "特選", "特秀", "一般", None):
            got = sell_plans_for(t, 9, rt)
            assert len(got) == 1, f"型{t}/9車/{rt} → {[p.key for p in got]}"


# ───────────────────────── 賭け金を作り直さない ─────────────────────────

def _row(bet_type: str, legs: list[dict]) -> dict:
    return {"bet_type": bet_type, "legs": legs}


def test_legs_keep_stakes_verbatim():
    """`type_lab_picks.legs` の賭け金がそのまま買い目行になる。"""
    from scripts.netkeirin_submit_type_lab import _legs_of

    legs_in = [{"combo": "1-4-5", "stake": 6400, "pred_odds": 2.6},
               {"combo": "1-4-7", "stake": 2100, "pred_odds": 9.9},
               {"combo": "1-4-6", "stake": 1500, "pred_odds": 16.2}]
    legs, odds = _legs_of(_row("trifecta", legs_in))
    assert [lg.stake_per_line for lg in legs] == [6400, 2100, 1500]
    assert sum(lg.stake_per_line for lg in legs) == 10_000
    # 1点=1行（各着1車ずつ）
    assert all(lg.groups == [[c] for c in combo]
               for lg, combo in zip(legs, [(1, 4, 5), (1, 4, 7), (1, 4, 6)]))
    assert odds[(1, 4, 5)] == 2.6


def test_trio_legs_are_single_combination_rows():
    """三連複は1点=1行（`trio_box` にソート済みの3車を1グループ）。"""
    from scripts.netkeirin_submit_type_lab import _legs_of

    legs, odds = _legs_of(_row("trio", [{"combo": "1=3=7", "stake": 6100,
                                         "pred_odds": 6.0}]))
    assert len(legs) == 1 and legs[0].groups == [[1, 3, 7]]
    assert legs[0].stake_per_line == 6100
    assert odds[frozenset({1, 3, 7})] == 6.0


def test_zero_stake_points_are_not_submitted():
    """賭け金 0 円の点は送らない（`allocate` が落とした点が復活しないこと）。"""
    from scripts.netkeirin_submit_type_lab import _legs_of

    legs, _ = _legs_of(_row("trifecta", [{"combo": "1-2-3", "stake": 0, "pred_odds": 900.0},
                                         {"combo": "1-2-4", "stake": 5000, "pred_odds": 4.0}]))
    assert [lg.groups for lg in legs] == [[[1], [2], [4]]]


def test_submit_script_never_reallocates_stakes():
    """入稿スクリプトが配分関数を呼ばないことを AST で固定する。

    🔴 `allocate` / `dutch_allocate` / `tilted_stakes` を呼んだ瞬間、
       検証した商品と売る商品が別物になりうる。買い目も金額も
       `type_lab_picks.legs` の値をそのまま送るのが設計。
    """
    tree = ast.parse(SUBMIT_PY.read_text(encoding="utf-8"))
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else
              getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    forbidden = {"allocate", "dutch_allocate", "tilted_stakes", "_build_tilted_legs",
                 "landing_weights", "unit_stake"}
    assert not (called & forbidden), f"配分をやり直している: {called & forbidden}"


# ───────────────────────── ゲート ─────────────────────────

def test_axis_gate_is_bound_from_backend_source():
    """軸信頼ゲートの閾値を写経していない（backend の正本を読み込む）。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "backend/src/services/keirin_type_lab_gate.py" in src
    # 閾値の数値がスクリプト側に書かれていないこと
    for v in ("1.537", "1.504", "1.480", "1.263", "1.245", "1.230"):
        assert v not in src, f"閾値 {v} を写している"


def test_mean_payout_gate_rejects_cheap_products():
    from scripts.netkeirin_submit_type_lab import _gate_reason

    cheap = {"pred_mean_payout": 19_999.0,
             "legs": [{"combo": "1-2-3", "stake": 10_000, "pred_odds": 2.0}]}
    assert _gate_reason(cheap) is not None
    ok = {"pred_mean_payout": 30_000.0,
          "legs": [{"combo": "1-2-3", "stake": 10_000, "pred_odds": 3.0}]}
    assert _gate_reason(ok) is None


def test_point_odds_gate_rejects_cheap_point():
    from scripts.netkeirin_submit_type_lab import _gate_reason

    row = {"pred_mean_payout": 30_000.0,
           "legs": [{"combo": "1-2-3", "stake": 5_000, "pred_odds": 1.9},
                    {"combo": "1-2-4", "stake": 5_000, "pred_odds": 30.0}]}
    assert _gate_reason(row) is not None


def test_gates_pass_when_unmeasurable():
    """判定できないものは通す（分からないことを理由に商品を落とさない）。"""
    from scripts.netkeirin_submit_type_lab import _gate_reason

    assert _gate_reason({"pred_mean_payout": None,
                         "legs": [{"combo": "1-2-3", "stake": 100, "pred_odds": None}]}) is None


# ───────────────────────── 既存経路を壊さない ─────────────────────────

def test_existing_rank_submitter_has_no_type_lab_branch():
    """既存の入稿スクリプトに**型ラボ固有の分岐が無い**。

    移行は `netkeirin_settings` の ON/OFF で行う（ロールバックが SQL 1本で済む）。
    既存側に型ラボ専用の分岐を入れると、戻すのにデプロイが要る。

    ⚠️ 2026-08-28 に `build_bet_detail` / `approve_and_submit` へ **汎用の**
       `act_type` 受け渡しを足した（勝負アイコンを商品と一緒に持ち回る）。
       型ラボへの参照は持たないので、この不変条件は保たれている。
    """
    src = (REPO / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert "type_lab" not in src


# ───────────────────────── 勝負アイコン ─────────────────────────

def test_only_type_f_pay_is_longshot():
    """穴狙いは `F_pay` だけ。**複数可**なので選定は要らない。

    🔴 **アイコンの表は入稿しうるプラン全体を覆うこと。** 9車の型F が売る
       `F_hit` が漏れると `.get(..., 既定)` で黙って既定へ落ちる。
    🔴 `F_hit` に穴狙いを付けていないのは 2026-08-30 のユーザー判断
       （「穴狙いのアイコンは現状のまま様子見」）。広げると効果の切り分けが
       さらに難しくなる。
    """
    from src.type_lab import SELLABLE_PLAN_KEYS
    from scripts.netkeirin_submit_type_lab import ACT_TYPE_BY_PLAN
    from src.netkeirin_client import ACT_TYPE_DEFAULT, ACT_TYPE_LONGSHOT

    assert set(ACT_TYPE_BY_PLAN) == set(SELLABLE_PLAN_KEYS), \
        "入稿しうるプランと表がずれている"
    assert ACT_TYPE_BY_PLAN["F_pay"] == ACT_TYPE_LONGSHOT
    assert all(v == ACT_TYPE_DEFAULT for k, v in ACT_TYPE_BY_PLAN.items()
               if k != "F_pay")


def test_sellable_plan_keys_covers_every_type_and_car_count():
    """🔴 `SELLABLE_PLAN_KEYS` は実際に売りうるキーを全部含む。

    ここが漏れると `_load_rows` の絞りでその商品が**黙って消える**。
    """
    from src.type_lab import SELLABLE_PLAN_KEYS

    for t in "ABCDEF":
        for n in (7, 9):
            for rt in ("決勝", "準決勝", "選抜", "一般", None):
                for pl in sell_plans_for(t, n, rt):
                    assert pl.key in SELLABLE_PLAN_KEYS, (t, n, rt, pl.key)


@pytest.mark.parametrize("n_entries,race_type", [(7, "決勝"), (7, "準決勝"),
                                                 (7, "特選"), (9, "決勝")])
def test_type_f_pay_is_longshot_regardless_of_car_count(n_entries, race_type):
    """🔴 `F_pay` を売るときのアイコンは**車数で変わらない**。

    ⚠️ 9車の**決勝以外**は `F_hit` を売るのでこの表には含めない（別テスト）。
    商品の性格とも一致する——9車決勝の `F_pay` は表示的中 2.99%（67件中2件）・
    払戻中央 169,545円 で、この体系で最も極端な一撃枠。

    ⚠️ 将来 `ACT_TYPE_BY_PLAN` を車数別に持つと、ここが落ちる。
       落ちたときは「車数で売り方を変えた」という設計変更の合図として扱うこと。
    """
    from scripts.netkeirin_submit_type_lab import ACT_TYPE_BY_PLAN
    from src.netkeirin_client import ACT_TYPE_LONGSHOT

    plans = sell_plans_for("F", n_entries, race_type)
    assert [p.key for p in plans] == ["F_pay"]
    assert ACT_TYPE_BY_PLAN[plans[0].key] == ACT_TYPE_LONGSHOT


def test_act_type_travels_in_bet_detail():
    """🔴 承認制では入稿時の act_type は使われないので、商品と一緒に保存する。"""
    from scripts.netkeirin_submit_type_lab import _legs_of
    from scripts.netkeirin_submit_wt import build_bet_detail
    from src.netkeirin_client import ACT_TYPE_LONGSHOT

    legs, odds = _legs_of(_row("trifecta", [{"combo": "1-4-5", "stake": 10_000,
                                             "pred_odds": 30.0}]))
    detail = json.loads(build_bet_detail(legs, source="type_lab", marks={1: "◎"},
                                         predicted_odds=odds,
                                         act_type=ACT_TYPE_LONGSHOT))
    assert detail["act_type"] == ACT_TYPE_LONGSHOT
    # 省略時は欄そのものを作らない（過去の行と形を揃える）
    plain = json.loads(build_bet_detail(legs, source="type_lab", marks={1: "◎"},
                                        predicted_odds=odds))
    assert "act_type" not in plain


def test_bet_detail_carries_car_count():
    """🔴 承認経路が使う車数を入稿データに入れる。

    承認は印の数から車数を導くフォールバックを持つが、型ラボは**買った車にだけ**
    印を付けるので `len(marks)` が車数より小さくなる。入れておかないと
    `submit_pick_multi` の「7/9車のみ対応」で**承認が丸ごと失敗する**
    （2026-08-28 に実際に踏みかけた: 印5車ぶんで n_cars=5 になった）。
    """
    from scripts.netkeirin_submit_type_lab import _legs_of
    from scripts.netkeirin_submit_wt import build_bet_detail

    legs, odds = _legs_of(_row("trifecta", [{"combo": "1-4-5", "stake": 10_000,
                                             "pred_odds": 30.0}]))
    # 印は買った3車だけ＝出走7車と一致しない
    marks = {1: "◎", 4: "○", 5: "▲"}
    detail = json.loads(build_bet_detail(legs, source="type_lab", marks=marks,
                                         predicted_odds=odds, n_cars=7))
    assert detail["n_cars"] == 7
    assert len(detail["marks"]) == 3, "印は買った車だけ（この前提が崩れたら設計変更）"


def test_submit_row_always_passes_car_count():
    """入稿経路が `n_cars` を渡していることを AST で固定する。"""
    tree = ast.parse(SUBMIT_PY.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "build_bet_detail"]
    assert calls, "build_bet_detail を呼んでいない"
    for c in calls:
        assert any(k.arg == "n_cars" for k in c.keywords), "n_cars を渡していない"


def test_car_count_fallback_order():
    """車数は ①ランク表 ②入稿データ ③印の数、の順。"""
    import inspect

    from scripts import netkeirin_submit_wt as m

    src = inspect.getsource(m.approve_and_submit)
    assert 'cfg.get("n_cars")' in src
    assert 'detail.get("n_cars")' in src
    assert "_n_cars_from_marks(marks)" in src
    i_cfg = src.index('cfg.get("n_cars")')
    i_det = src.index('detail.get("n_cars")')
    i_mark = src.index("_n_cars_from_marks(marks)")
    assert i_cfg < i_det < i_mark, "フォールバックの順序が違う"


def test_act_type_priority():
    """優先順位: ランク表 > 自信あり > 商品が持つ指定 > 既定。

    🔴 「自信あり」は1日1件の明示的な選定、穴狙いは複数可。**譲るのは複数可の側**。
    🔴 ランク表が最優先なのは従来どおり（既存ランクの挙動を変えない）。
    """
    from scripts.netkeirin_submit_wt import resolve_act_type
    from src.netkeirin_client import (ACT_TYPE_CONFIDENT, ACT_TYPE_DEFAULT,
                                      ACT_TYPE_LONGSHOT)

    assert resolve_act_type(ACT_TYPE_LONGSHOT, True, None) == ACT_TYPE_LONGSHOT
    assert resolve_act_type(ACT_TYPE_LONGSHOT, False, ACT_TYPE_DEFAULT) == ACT_TYPE_LONGSHOT
    assert resolve_act_type(None, True, ACT_TYPE_LONGSHOT) == ACT_TYPE_CONFIDENT
    assert resolve_act_type(None, False, ACT_TYPE_LONGSHOT) == ACT_TYPE_LONGSHOT
    assert resolve_act_type(None, False, None) == ACT_TYPE_DEFAULT


def test_bet_detail_is_json_with_lines():
    """記録する `bet_detail` は既存ランクと同じ形（確認画面が読む）。"""
    from scripts.netkeirin_submit_type_lab import _legs_of
    from scripts.netkeirin_submit_wt import build_bet_detail

    legs, odds = _legs_of(_row("trifecta", [{"combo": "1-4-5", "stake": 6400,
                                             "pred_odds": 2.6}]))
    detail = json.loads(build_bet_detail(legs, source="type_lab", marks={1: "◎"},
                                         predicted_odds=odds))
    assert detail["lines"][0]["combo"] == "1-4-5"
    assert detail["lines"][0]["stake"] == 6400
    assert detail["lines"][0]["odds"] == 2.6


def test_dry_run_never_rebuilds(monkeypatch):
    """🔴 dry-run は `type_lab_picks` を書き換えない。

    昼・夕の回は「まだ入稿していないレース」の買い目を組み直してから入稿するが、
    組み直しは DB への書き込みなので、dry-run で走ると
    「何も変えずに中身を見る」という約束が破れる。2026-08-28 の初回検証で
    実際に本番の行を書き換えた（そのときは未入稿だったので実害なし）。
    """
    from scripts import netkeirin_submit_type_lab as m

    called: list[tuple] = []
    monkeypatch.setattr(m, "rebuild", lambda *a, **k: called.append(a))
    monkeypatch.setattr(m, "_load_settings", lambda: {})
    monkeypatch.setattr(m, "_approval_required", lambda: False)
    monkeypatch.setattr(m, "_load_closed_races", lambda day: set())
    monkeypatch.setattr(m, "_already_submitted", lambda keys: set())
    monkeypatch.setattr(m, "_missing_market_inputs", lambda rk: None)
    monkeypatch.setattr(m, "_build_entry_table", lambda rk, marks: None)
    monkeypatch.setattr(m, "_load_rows", lambda day: [{
        "race_key": "20260828_13_05", "race_date": "2026-08-28", "venue_name": "松阪",
        "race_no": 5, "race_type": "予選", "n_entries": 7, "cup_grade": None,
        "type_label": "A", "axis_sum": 1.9, "axis1": 1, "axis2": 4,
        "p3_order": "1-4-5-6-7-2-3", "plan_key": "A_hit", "bet_type": "trifecta",
        "n_legs": 3, "budget": 10_000, "pred_mean_payout": 30_000.0,
        "pred_min_payout": 25_000.0,
        "legs": [{"combo": "1-4-5", "stake": 6400, "pred_odds": 2.6},
                 {"combo": "1-4-7", "stake": 2100, "pred_odds": 9.9},
                 {"combo": "1-4-6", "stake": 1500, "pred_odds": 16.2}],
    }])

    # 🔴 **本物の `NetkeirinClient` を作らせない。** 作ると login() から
    #    実際に netkeirin へ HTTP が飛ぶ（テストが商品を出しうる）。
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def submit_pick_multi(self, **k):
            return False, "test"

    monkeypatch.setattr(m, "NetkeirinClient", _FakeClient)
    monkeypatch.setattr(m, "send", lambda *a, **k: None)

    m.run("2026-08-28", "noon", dry_run=True, only_key=None, do_rebuild=True)
    assert called == [], "dry-run なのに組み直し（DB書き込み）が走った"

    m.run("2026-08-28", "noon", dry_run=False, only_key=None, do_rebuild=True)
    assert called, "本番実行では組み直しが走るべき"


def test_dry_run_records_no_skips(monkeypatch):
    """🔴 dry-run は `submission_skips` にも書かない。

    見送りの記録も DB への書き込みなので、dry-run で通すと
    「何も変えずに中身を見る」という約束が破れる。
    """
    from scripts import netkeirin_submit_type_lab as m

    def _boom(*a, **k):
        raise AssertionError("dry-run で _skip が呼ばれた")

    monkeypatch.setattr(m, "_skip", _boom)
    dry = m._make_skip(True)
    dry("20260828_13_05", "A_hit", "morning", "closed", "締切", "松阪", 5)
    assert m._make_skip(False) is m._skip


def test_marquee_races_bypass_the_axis_gate():
    """看板レースは軸信頼ゲートを素通しする（「看板は必ず出す」を優先）。

    ⚠️ 素通しは**軸信頼ゲートだけ**。平均払戻・1点オッズは看板にも掛ける。
    """
    from scripts.netkeirin_submit_type_lab import _passes_axis_gate

    weak = {"plan_key": "A_hit", "axis_sum": 1.0, "n_entries": 7,
            "race_type": "予選", "cup_grade": None}
    assert _passes_axis_gate(weak) is False
    assert _passes_axis_gate({**weak, "race_type": "決勝"}) is True
    assert _passes_axis_gate({**weak, "race_type": "特選"}) is True


def test_races_taken_by_other_ranks_is_derived_from_already():
    """🔴 別ランクが取ったレースを正しく拾う（netkeirin は1レース1商品）。

    `_already_submitted()` は**ランクを絞らず全ランクの行を返す**。初版は
    そこに気づかず専用クエリの結果から `already` の race_key を差し引いており、
    **常に空集合**＝ガードが一度も効かなかった（2026-08-28 の dry-run で発覚）。
    """
    from scripts.netkeirin_submit_type_lab import races_taken_by_other_ranks

    already = {("R1", "7S"), ("R2", "A_hit"), ("R3", "9C"), ("R2", "7C")}
    assert races_taken_by_other_ranks(already) == {"R1", "R3", "R2"}
    # 型ラボだけが取っているレースは「別ランクが取った」に入らない
    assert races_taken_by_other_ranks({("R9", "F_pay")}) == set()
    assert races_taken_by_other_ranks(set()) == set()


def test_races_taken_by_other_ranks_are_skipped(monkeypatch):
    """別ランクが取ったレースには出さない（ループ側の結線）。"""
    from scripts import netkeirin_submit_type_lab as m

    row = {
        "race_key": "20260829_13_05", "race_date": "2026-08-29", "venue_name": "松阪",
        "race_no": 5, "race_type": "予選", "n_entries": 7, "cup_grade": None,
        "type_label": "A", "axis_sum": 1.9, "axis1": 1, "axis2": 4,
        "p3_order": "1-4-5-6-7-2-3", "plan_key": "A_hit", "bet_type": "trifecta",
        "n_legs": 1, "budget": 10_000, "pred_mean_payout": 30_000.0,
        "pred_min_payout": 30_000.0,
        "legs": [{"combo": "1-4-5", "stake": 10_000, "pred_odds": 3.0}],
    }
    monkeypatch.setattr(m, "_load_settings", lambda: {})
    monkeypatch.setattr(m, "_approval_required", lambda: False)
    monkeypatch.setattr(m, "_load_closed_races", lambda day: set())
    monkeypatch.setattr(m, "_missing_market_inputs", lambda rk: None)
    monkeypatch.setattr(m, "_build_entry_table", lambda rk, marks: None)
    monkeypatch.setattr(m, "_load_rows", lambda day: [row])

    submitted: list = []
    monkeypatch.setattr(m, "submit_row", lambda *a, **k: (submitted.append(a) or (True, "t")))

    # 既存ランク 7S が取っている → 出さない
    monkeypatch.setattr(m, "_already_submitted", lambda keys: {("20260829_13_05", "7S")})
    m.run("2026-08-29", "morning", dry_run=True, only_key=None, do_rebuild=False)
    assert submitted == [], "別ランクが取ったレースへ出そうとした"

    # 誰も取っていない → 出す
    monkeypatch.setattr(m, "_already_submitted", lambda keys: set())
    m.run("2026-08-29", "morning", dry_run=True, only_key=None, do_rebuild=False)
    assert submitted, "誰も取っていないのに出さなかった"


# ── 組み直しで型が変わったときの古い行（2026-08-29）──────────────────
#
# 🔴 一意キーが `(race_key, plan_key, mode)` なので、型が F→C に変わると
#    `C_hit` の行が増えるだけで **`F_pay` の行が残る**。売る／売らないは
#    行ごとの `type_label` で決めるため、古い行も「型Fだから F_pay を売って
#    よい」と通り、**1レースに2商品**が入稿された（2026-08-29 昼・4レース）。
#    生成側で消し、読む側で絞り、入稿ループでも止める（多重防御）。


def test_生成側が型の変わった古いプランを消す():
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "_drop_stale_plans" in src, "古いプランを消す処理がありません"
    i = src.index("def _drop_stale_plans(")
    block = src[i:src.index("\ndef ", i + 10)]
    assert "DELETE FROM type_lab_picks" in block
    assert "plan_key NOT IN" in block, "今回組んだプラン以外を消す形になっていません"
    assert "settled_at IS NULL" in block, (
        "採点済みの行まで消しています。売って結果まで入った行は検証台の実績で、"
        "消すと後から復元できません")
    # save() から必ず呼ばれること（呼ばれない実装だと存在しても効かない）
    j = src.index("def save(")
    assert "_drop_stale_plans(" in src[j:j + 1200], "save() から呼ばれていません"


def test_読む側が最新の型だけに絞る():
    src = SUBMIT_PY.read_text(encoding="utf-8")
    i = src.index("def _load_rows(")
    block = src[i:src.index("\ndef ", i + 10)]
    assert "generated_at" in block, "最新の行を選ぶための列を取っていません"
    assert 'current[' in block and "continue" in block, \
        "古い型の行を落としていません"


def test_型ラボ自身が取ったレースには出さない():
    """`(race_key, plan) in already` は**同じプラン**の二重入稿しか止めない。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "taken_by_type_lab" in src, "型ラボ自身の重複ガードがありません"
    i = src.index("for row in sorted(rows")
    loop = src[i:i + 2500]
    assert "if race_key in taken_by_type_lab:" in loop, "ループで見ていません"
    # 入稿できたら同じ実行の中でも二度目を止める（`already` は開始時の断面）
    assert "taken_by_type_lab.add(race_key)" in src, \
        "同じ実行の中で2つ目のプランが通ってしまいます"
    assert src.index("if race_key in taken_by_type_lab:") \
        < src.index("taken_by_type_lab.add(race_key)"), "判定より後に登録しています"


class _FakeCursor:
    rowcount = 1


class _FakeConn:
    """`execute` を記録するだけの接続。DELETE の形を実際に見る。"""

    def __init__(self, rows=()):
        self.calls: list[tuple[str, tuple]] = []
        self._rows = list(rows)

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        cur = _FakeCursor()
        cur.fetchall = lambda: self._rows          # noqa: E731 — テスト用の簡易版
        return cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_古いプランの削除は今回のプランを残す():
    """型が F→C に変わったレースで、`C_hit` は残し `F_pay` を消す形になるか。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_type_lab_picks", REPO / "scripts" / "build_type_lab_picks.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    conn = _FakeConn()
    m._drop_stale_plans(conn, [{"race_key": "20260829_73_01", "mode": "live",
                                "plan_key": "C_hit"}])
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert sql.startswith("DELETE FROM type_lab_picks")
    assert params == ("20260829_73_01", "live", "C_hit"), \
        "今回組んだプランが除外リストに入っていません（自分を消してしまう）"


def test_古いプランの削除はレースとモードごとにまとまる():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_type_lab_picks", REPO / "scripts" / "build_type_lab_picks.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    conn = _FakeConn()
    m._drop_stale_plans(conn, [
        {"race_key": "r1", "mode": "live", "plan_key": "A_hit"},
        {"race_key": "r1", "mode": "live", "plan_key": "A_pay"},
        {"race_key": "r2", "mode": "live9", "plan_key": "F_pay"},
    ])
    assert len(conn.calls) == 2, "レース×モードごとに1回にまとめていません"
    r1 = next(c for c in conn.calls if c[1][0] == "r1")
    assert r1[1] == ("r1", "live", "A_hit", "A_pay")


def test_読み出しは古い型の行を落とす(monkeypatch):
    """同じレースに型Fの古い行と型Cの新しい行があるとき、型Cだけ返す。"""
    import datetime as _dt

    import scripts.netkeirin_submit_type_lab as m

    base = dict(race_date="2026-08-29", venue_name="小松島", race_no=1,
                race_type="ガールズ一般", n_entries=7, day_index=1, axis_sum=1.5,
                axis1=1, axis2=2, p3_order=None, mode="live", bet_type="trifecta",
                n_legs=4, budget=10000, legs="[]", pred_mean_payout=30000,
                pred_min_payout=20000, rule_version="x", cup_grade=None)
    old = dict(base, race_key="R1", type_label="F", plan_key="F_pay",
               generated_at=_dt.datetime(2026, 8, 29, 7, 16))
    new = dict(base, race_key="R1", type_label="C", plan_key="C_hit",
               generated_at=_dt.datetime(2026, 8, 29, 13, 6))
    monkeypatch.setattr(m, "get_connection", lambda: _FakeConn([old, new]))

    got = m._load_rows("2026-08-29")
    assert [r["plan_key"] for r in got] == ["C_hit"], \
        "組み直し前の型の行が残っています（1レース2商品になります）"
