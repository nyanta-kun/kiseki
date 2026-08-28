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


def test_nine_car_type_f_is_final_only_and_pay():
    """9車の型F は**決勝だけ**・**F_pay**。

    🔴 決勝限定は「売る／売らない」の絞りで、hit/pay の分岐ではない。
       9車の型F を全部売ると表示的中 6.07%・ROI 60.5% で壁を大きく下回る。
    """
    assert [p.key for p in sell_plans_for("F", 9, "決勝")] == ["F_pay"]
    assert sell_plans_for("F", 9, "準決勝") == []
    assert sell_plans_for("F", 9, None) == []
    # 型F以外は9車でも種別によらず売る
    assert [p.key for p in sell_plans_for("A", 9, "特選")] == ["A_hit"]


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

def test_only_type_f_is_longshot():
    """穴狙いは型F（`F_pay`）だけ。**複数可**なので選定は要らない。"""
    from scripts.netkeirin_submit_type_lab import ACT_TYPE_BY_PLAN
    from src.netkeirin_client import ACT_TYPE_DEFAULT, ACT_TYPE_LONGSHOT

    assert set(ACT_TYPE_BY_PLAN) == set(SELL_PLANS), "売るプランと表がずれている"
    assert ACT_TYPE_BY_PLAN["F_pay"] == ACT_TYPE_LONGSHOT
    assert all(v == ACT_TYPE_DEFAULT for k, v in ACT_TYPE_BY_PLAN.items()
               if k != "F_pay")


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
