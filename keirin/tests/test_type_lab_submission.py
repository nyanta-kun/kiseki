"""型ラボの入稿データ（`src/type_lab_submission.py`）の回帰テスト。

ここで固定するのは、崩れると**商品説明が事実と食い違う**点:

  1. 印は**実際に買った車だけ**に付く（型D の「外した相手」に印が付かない）
  2. 配分の説明は `legs` から導く（均等に置いたのに「オッズに応じて配分」と言わない）
  3. 確率上位から積むプランに「軸2車流し」と書かない
     （実測で 56〜73% の目が ◎○ を1・2着に置いていない）
  4. 文面へ Markdown の装飾を書かない（netkeirin は Markdown を解釈しない）
  5. 文面へ数字（的中率・ROI・払戻）を焼き込まない（窓を変えると嘘になる）
  6. `PLANS`（`src/type_lab.py`）と文面の表が1対1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.type_lab import PLANS  # noqa: E402
from src.type_lab_submission import (  # noqa: E402
    CLOSING, PLAN_BODIES, PLAN_TITLES, STAKE_UNIT, TYPE_NOTES, TYPE_VIEWS,
    alloc_note, build_comment, build_submission, build_title, marks_for,
)


def _legs(*combos, stake=1000):
    return [{"combo": c, "stake": stake, "pred_odds": 10.0, "prob": 0.01} for c in combos]


# ───────────────────────── 印 ─────────────────────────

def test_marks_skip_cars_that_are_not_bought():
    """🔴 型D は「相手のうち最人気の1車を外す」のが設計の核心。

    外した車に印を付けると**買っていない車を推している**ことになる。
    実データ（奈良3R 2026-08-26）: 指数順 3-1-7-4-2-5-6 で 4番を外した形。
    """
    order = "3-1-7-4-2-5-6"
    legs = _legs("1=3=7", "1=2=3", "1=3=6", "1=3=5")
    marks = marks_for(order, legs, 3, 1)
    assert marks == {3: "◎", 1: "○", 7: "▲", 2: "△", 5: "△", 6: "△"}
    assert 4 not in marks, "買っていない4番に印が付いている"


def test_marks_rank_partners_by_index_order_not_car_number():
    """▲ は買い目に出てくる非軸車のうち**指数最上位**。車番順ではない。"""
    marks = marks_for("1-4-5-7-6-3-2", _legs("1-4-5", "1-4-7", "1-4-6"), 1, 4)
    assert marks[5] == "▲"          # 指数3位
    assert marks[7] == "△" and marks[6] == "△"


def test_marks_handle_trio_and_trifecta_combos():
    """三連複 `2=5=7` と三連単 `1-4-5` の両方から車番を読めること。"""
    assert set(marks_for("7-5-6-1-2-4-3", _legs("2=5=7", "1=5=7"), 7, 5)) == {7, 5, 1, 2}
    assert set(marks_for("1-4-5-2-3-7-6", _legs("1-4-5", "1-4-2"), 1, 4)) == {1, 4, 5, 2}


def test_marks_do_not_invent_an_order_for_unknown_cars():
    """`p3_order` に無い車が買い目に出ても ▲ を奪わない（順序を決められないため）。"""
    marks = marks_for("1-4-5", _legs("1-4-5", "1-4-9"), 1, 4)
    assert marks[5] == "▲" and marks[9] == "△"


# ───────────────────────── 配分の説明 ─────────────────────────

def test_alloc_note_calls_rounding_remainder_equal():
    """🔴 端数だけで「傾斜」と言わない（10,000円/3点 → 3,400/3,300/3,300）。"""
    legs = [{"stake": 3400}, {"stake": 3300}, {"stake": 3300}]
    assert "均等" in alloc_note(legs) and "オッズに応じて" not in alloc_note(legs)


def test_alloc_note_detects_real_tilt():
    legs = [{"stake": 6400}, {"stake": 2100}, {"stake": 1500}]
    assert alloc_note(legs).startswith("金額は均等ではなく")


def test_alloc_note_boundary_is_one_stake_unit():
    assert "均等" in alloc_note([{"stake": 3000}, {"stake": 3000 + STAKE_UNIT}])
    assert alloc_note([{"stake": 3000}, {"stake": 3000 + STAKE_UNIT + 1}]).startswith("金額は均等ではなく")


def test_alloc_note_is_empty_without_stakes():
    """賭け金が読めないときは**何も言わない**（推測で配分を説明しない）。"""
    assert alloc_note([]) == ""


# ───────────────────────── 文面 ─────────────────────────

#: 確率上位から積むプラン。買い目の 56〜73% が ◎○ を1・2着に置いていない
#: （実測 2026-06〜08）ので、「軸2車流し」と書いてはいけない。
PROB_TOP_PLANS = ("B_hit", "C_hit", "E_hit")


@pytest.mark.parametrize("plan", PROB_TOP_PLANS)
def test_prob_top_plans_do_not_claim_axis_flow(plan):
    body = PLAN_BODIES[plan]
    assert "軸2車流し" not in body and "2車軸" not in body, plan
    # 逆に「◎○が2着・3着へ回る」ことは必ず書く（買い手の期待を外さないため）
    assert "2着" in body and "3着" in body, plan


def test_axis_flow_plans_do_say_so():
    """◎○を軸に据えるプラン（型D）は、そう書いてあること。"""
    assert "2車軸" in PLAN_BODIES["D_hit"]


@pytest.mark.parametrize("text", list(PLAN_BODIES.values()) + list(TYPE_NOTES.values())
                         + list(TYPE_VIEWS.values()) + [CLOSING])
def test_no_markdown_emphasis(text):
    """🔴 netkeirin は Markdown を解釈しない。`**` はそのまま商品説明に出る。"""
    assert "**" not in text and "__" not in text


@pytest.mark.parametrize("text", list(PLAN_BODIES.values()) + list(TYPE_NOTES.values()))
def test_no_baked_in_numbers(text):
    """🔴 的中率・ROI・払戻を焼き込まない（窓を変えると事実と食い違う）。

    ⚠️ 「2着」「3着」のような着順は数字ではないので許す。
    """
    assert not re.search(r"\d+(\.\d+)?\s*[%％]", text), text
    assert not re.search(r"\d[\d,]*\s*円", text), text
    assert "ROI" not in text and "回収率" not in text


def test_every_plan_has_a_title_and_body():
    """🔴 `PLANS` を増やしたら文面も増やすこと（片方だけだと空文字で出る）。"""
    assert set(PLAN_TITLES) == set(PLANS), set(PLAN_TITLES) ^ set(PLANS)
    assert set(PLAN_BODIES) == set(PLANS), set(PLAN_BODIES) ^ set(PLANS)


def test_every_type_has_a_view_and_note():
    types = {p.type_label for p in PLANS.values()}
    assert types <= set(TYPE_VIEWS) and types <= set(TYPE_NOTES)


def test_title_is_two_blocks():
    assert build_title("A_hit", "A") == "本線の三連単｜二軸が堅い一戦"


@pytest.mark.parametrize("plan", sorted(PLANS))
def test_title_never_leaks_a_placeholder(plan):
    t = build_title(plan, PLANS[plan].type_label)
    assert "{" not in t and "}" not in t and t.count("｜") == 1


# ───────────────────────── 組み立て ─────────────────────────

def test_comment_states_the_actual_point_count():
    """点数は文面に固定で書かず、実際の `legs` から差し込む（欠車で減るため）。"""
    c = build_comment("A_hit", "A", 1, 4, _legs("1-4-5", "1-4-7"), "trifecta")
    assert "三連単 2点。" in c
    c3 = build_comment("A_hit", "A", 1, 4, _legs("1-4-5", "1-4-7", "1-4-6"), "trifecta")
    assert "三連単 3点。" in c3


def test_comment_uses_the_right_bet_type_word():
    assert "三連複 4点。" in build_comment(
        "D_hit", "D", 3, 1, _legs("1=3=7", "1=2=3", "1=3=6", "1=3=5"), "trio")


def test_comment_shows_the_axes_it_actually_marks():
    """【二軸】の車番と ◎○ の車番が食い違わないこと。"""
    row = {"plan_key": "A_hit", "type_label": "A", "axis1": 1, "axis2": 4,
           "p3_order": "1-4-5-7-6-3-2", "bet_type": "trifecta",
           "legs": _legs("1-4-5", "1-4-7", "1-4-6")}
    sub = build_submission(row)
    assert "◎1番・○4番" in sub["comment"]
    assert sub["marks"][1] == "◎" and sub["marks"][4] == "○"


def test_entry_table_is_appended_only_when_given():
    row = {"plan_key": "A_hit", "type_label": "A", "axis1": 1, "axis2": 4,
           "p3_order": "1-4-5", "bet_type": "trifecta", "legs": _legs("1-4-5")}
    assert "参考データ" not in build_submission(row)["comment"]
    assert "参考データ" in build_submission(row, "<table></table>")["comment"]


def test_module_imports_only_the_standard_library():
    """🔴 backend 側からファイル読み込みで束縛する余地を残す（看板判定と同じ制約）。"""
    import ast
    src = (REPO / "src" / "type_lab_submission.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in {"typing"}, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in {"typing", "__future__"}, node.module


def test_preview_script_reuses_the_production_entry_table():
    """🔴 出走表HTMLを写し取らないこと（本番の列が変わると静かに食い違う）。"""
    src = (REPO / "scripts" / "preview_type_lab_submission.py").read_text(encoding="utf-8")
    assert "from netkeirin_submit_wt import _build_entry_table" in src
    assert "<table>" not in src, "出走表HTMLを自前で組んでいる"


def test_preview_script_never_writes():
    """🔴 プレビューは表示だけ。DB を書き換えず、入稿もしない。"""
    src = (REPO / "scripts" / "preview_type_lab_submission.py").read_text(encoding="utf-8")
    for banned in ("INSERT", "UPDATE", "DELETE", "NetkeirinClient", "submit_pick"):
        assert banned not in src, banned


def test_plan_notes_do_not_contradict_the_buy_description():
    """🔴 冒頭のレース見解と【買い目】が矛盾しないこと。

    型A の見解は「着順まで踏み込んで狙える」と言い切っている。着順を決め打ち
    しない `A_trio` と、◎を買わない `A_ana` は **`PLAN_NOTES` で上書きが要る**。
    上書きを消すと型A の見解がそのまま出て、直後の説明と食い違う。
    """
    from src.type_lab_submission import PLAN_BODIES, PLAN_NOTES, TYPE_NOTES

    assert "着順まで踏み込んで" in TYPE_NOTES["A"], "型A の見解の前提が変わった"
    for plan in ("A_trio", "A_ana"):
        note = PLAN_NOTES.get(plan)
        assert note, f"{plan} は PLAN_NOTES での上書きが要る"
        assert "着順まで踏み込んで" not in note, plan
    # `A_trio` は「決め打ちしない」で揃っていること。
    assert "決め打ち" in PLAN_NOTES["A_trio"] and "決め打ち" in PLAN_BODIES["A_trio"]
    # `A_ana` は本文でも見解でも ◎ を推していないこと。
    assert "◎" not in PLAN_NOTES["A_ana"] and "◎" not in PLAN_BODIES["A_ana"]


# ────────── 型の見解に買い方を書かない（2026-09-03・実害から） ──────────

def test_type_notes_do_not_describe_the_bet_construction():
    """🔴🔴 **`TYPE_NOTES` / `TYPE_VIEWS` に買い方を書かない。**

    型の見解は**同じ型の全プランで共有される**（型F なら `F_hit` 12点 /
    `F_pay` 4点 / `F_sign` 2〜3点）。買い方に踏み込むと必ずどれかと矛盾する。

    実害: 2026-08-31 に `F_hit` を `all6`（全6順列）から `prob_top`（確率上位12点）へ
    替えたとき、`PLAN_BODIES["F_hit"]` は直したが `TYPE_NOTES["F"]` の
    **「順番を決め打ちしない組み立てにしています」が取り残された**。
    実測（2026-08-29 以降の実入稿）で全6順列を買っている組の割合は
    F_hit 30.6% / F_pay 0.0% / F_sign 0.0% ＝ **三連単3点の `F_sign` とは正面から矛盾**。

    買い方は `PLAN_BODIES` の役割。ここは「レースをどう読んだか」だけにする。
    """
    from src.type_lab_submission import TYPE_NOTES, TYPE_VIEWS

    # 買い方に踏み込む語。**買い目の作りを説明する語だけ**を並べる
    # （「読み」に使う語＝拮抗・混戦・抜けた などは対象外）。
    banned = ("組み立て", "買い目", "点数", "流し", "軸2車", "三連単", "三連複",
              "固定し", "順番を決め打ち", "配分")
    for label, text in list(TYPE_NOTES.items()) + list(TYPE_VIEWS.items()):
        for w in banned:
            assert w not in text, (
                f"TYPE_NOTES/TYPE_VIEWS['{label}'] に買い方の記述『{w}』があります。"
                " 型の見解は同じ型の全プランで共有されるので、買い方は PLAN_BODIES へ")


def test_plan_bodies_cover_every_sellable_plan():
    """🔴 売りうるプランは全部 `PLAN_TITLES` / `PLAN_BODIES` を持つこと。

    欠けると `build_submission` が KeyError で落ちる＝**その商品だけ入稿できない**。
    看板枠（`{型}_sign`）はループで生やしているので、型を増やしたら自動で付く。
    """
    from src.type_lab import SELLABLE_PLAN_KEYS
    from src.type_lab_submission import PLAN_BODIES, PLAN_TITLES

    for key in SELLABLE_PLAN_KEYS:
        assert key in PLAN_TITLES, f"{key} のタイトルがありません"
        assert key in PLAN_BODIES, f"{key} の本文がありません"


def test_role_base_matches_the_source_of_truth():
    """役割の定数は `src.type_lab` と同じ値であること。

    🔴 このモジュールは標準ライブラリしか import できない（上のテスト）ので
       定数を**複製している**。ずれると押さえの目に印が付き、点数の説明も狂う。
    """
    from src.type_lab import ROLE_BASE as SRC_ROLE_BASE
    from src.type_lab_submission import ROLE_BASE, _base_legs

    assert ROLE_BASE == SRC_ROLE_BASE
    legs = [{"combo": "1-2-3", "stake": 8000},
            {"combo": "6-7-5", "stake": 2000, "role": "band"}]
    assert _base_legs(legs) == legs[:1]
