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
    """`SELL_PLANS` は A〜E だけ。**型F はここに書かない**（2026-08-31）。

    型F は種別で `F_pay`（決勝）/ `F_hit`（それ以外）に分かれるので固定の集合では
    表せない。判定の正本は `sell_plans_for` の型F 分岐。
    """
    assert SELL_PLANS == ("A_hit", "A_trio", "A_ana",
                          "B_hit", "C_hit", "D_hit", "E_hit")
    assert "F_pay" not in SELL_PLANS and "F_hit" not in SELL_PLANS
    assert set(SELL_PLANS) <= set(PLANS), "SELL_PLANS に PLANS 外のキーがある"


def test_type_f_splits_by_race_type():
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
    # 🔴 **7車は看板枠が先に来る**（2026-09-01 再投入）。`SIGNBOARD_RACE_TYPES`
    #    （決勝系＋特選）に入る種別は `F_sign`、それ以外が `F_hit`。
    assert [p.key for p in sell_plans_for("F", 7, "決勝")] == ["F_sign"]
    assert [p.key for p in sell_plans_for("F", 7, "チャレンジ決勝")] == ["F_sign"]
    assert [p.key for p in sell_plans_for("F", 7, "準決勝")] == ["F_sign"]
    assert [p.key for p in sell_plans_for("F", 7, "特選")] == ["F_sign"]
    # 看板枠の対象外の種別は当たる回数を売る側のまま
    assert [p.key for p in sell_plans_for("F", 7, "選抜")] == ["F_hit"]
    assert [p.key for p in sell_plans_for("F", 7, "一般")] == ["F_hit"]
    assert [p.key for p in sell_plans_for("F", 7, "予選")] == ["F_hit"]
    # 🔴 **看板枠は7車だけ**（`SIGNBOARD_N_ENTRIES`）。9車は従来どおり種別で分かれる。
    #    ここで「準決勝」を部分一致で拾わないことも一緒に固定する
    #    （拾うと表示的中の低い `F_pay` が母集団の何倍にも広がる）。
    assert [p.key for p in sell_plans_for("F", 9, "準決勝")] == ["F_hit"]
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


def test_every_sellable_plan_has_an_axis_gate_threshold():
    """🔴 **売りうるプランは全部 `AXIS_GATE_MIN` に閾値を持つこと。**

    `passes_axis_gate` は「判定できないものは通す」設計なので、閾値表に鍵が
    無いプランは**エラーを出さずにゲートを素通りする**。2026-08-31 に型A を
    3分割（PR#384）したとき、この表だけが更新されず `A_ana` / `A_trio` が
    素通りしていた——実測で型A の入稿 21件のうち **10件（48%）**がゲート未通過、
    うち4件は `A_hit` の閾値を下回っていた。

    プランを増やすたびに人が気づく必要がある形にしない。
    ⚠️ 看板枠（`*_sign`）も `SIGNBOARD_TYPES` に入っていれば売るので対象に含める
       （2026-09-01 に型F の看板枠を再投入した）。
    """
    import importlib.util

    from src.type_lab import SELLABLE_PLAN_KEYS

    path = REPO.parent / "backend/src/services/keirin_type_lab_gate.py"
    spec = importlib.util.spec_from_file_location("tl_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    missing = sorted(k for k in SELLABLE_PLAN_KEYS if k not in gate.AXIS_GATE_MIN)
    assert not missing, (
        f"閾値が無いまま売れてしまうプラン: {missing}。"
        f"探索窓 {gate.AXIS_GATE_SOURCE_WINDOW} のプラン内 p20 を引いて "
        f"AXIS_GATE_MIN へ足すこと")


def test_axis_gate_thresholds_are_in_a_sane_range():
    """閾値は 3着内率2車の合計なので 0〜2。桁を間違えたら落とす。"""
    import importlib.util

    path = REPO.parent / "backend/src/services/keirin_type_lab_gate.py"
    spec = importlib.util.spec_from_file_location("tl_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    for k, v in gate.AXIS_GATE_MIN.items():
        assert 1.0 < float(v) < 2.0, f"{k} の閾値 {v} が範囲外"


def test_every_sellable_plan_has_priority_quantiles():
    """🔴 **上限に当たったときの順位付けも、売りうるプラン全部が持つこと。**

    `axis_priority` は知らないプランに 0.5 を返す。1つだけ 0.5 のプランがあると
    **そのプランだけが常に中央へ寄る**（他が 0〜1 に散るので、上限が浅いときは
    必ず残り、深いときは必ず消える）という偏りが静かに入る。
    """
    import importlib.util

    from src.type_lab import SELLABLE_PLAN_KEYS

    path = REPO.parent / "backend/src/services/keirin_type_lab_gate.py"
    spec = importlib.util.spec_from_file_location("tl_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    # 🔴 **7車で売りうるプランだけが対象**。9車は上限で落とさない設計なので
    #    分位表を持たない（`_priority` が 1.0 を返す）。`F_pay` は 9車決勝専用。
    sellable_7 = {p.key
                  for t_ in "ABCDEF"
                  for rt in ("決勝", "チャレンジ決勝", "準決勝", "特選", "初特選",
                             "選抜", "一般", "予選", None)
                  for p in sell_plans_for(t_, 7, rt)}
    sellable_7 |= {"A_ana", "A_trio"}          # 型A の3分岐（pw_ent / trio_ok で選ばれる）
    assert sellable_7 <= set(SELLABLE_PLAN_KEYS)
    missing = sorted(k for k in sellable_7 if k not in gate.AXIS_PRIORITY_QUANTILES)
    assert not missing, f"優先度の分位表が無いプラン: {missing}"
    for k, qs in gate.AXIS_PRIORITY_QUANTILES.items():
        assert len(qs) >= 2, f"{k} の分位が少なすぎる"
        assert list(qs) == sorted(qs), f"{k} の分位が昇順でない"


def test_daily_cap_exempts_finals_but_not_all_marquee():
    """🔴 **上限の対象外は「決勝・準決勝＋グレード3以上」だけ。**

    看板（`is_fill_target`）ぜんぶを外すと、特選・選抜に出る `F_sign`
    （設計上 表示的中 5.28% の一撃商品）が全部残り、上限の狙い
    「当たりやすい側を残す」と正面から衝突する——実測で対照20本に
    ROI 8/20・11/20 と負ける（決勝＋準決勝なら 12/20・19/20）。
    """
    import importlib.util

    from src.marquee import is_fill_target

    path = REPO.parent / "backend/src/services/keirin_type_lab_gate.py"
    spec = importlib.util.spec_from_file_location("tl_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    # 決勝・準決勝は残す
    for rt in ("決勝", "準決勝", "チャレンジ決勝", "チャレンジ準決勝", "ガールズ決勝"):
        assert gate.daily_cap_exempt(rt), rt
    # 看板ではあるが上限の対象にする（ここが `is_fill_target` との違い）
    for rt in ("特選", "初特選", "選抜", "特秀"):
        assert is_fill_target(rt), f"{rt} は看板のはず（前提の確認）"
        assert not gate.daily_cap_exempt(rt), f"{rt} を上限の対象外にしている"
    # グレードは看板の正本と同じ境界
    assert gate.DAILY_CAP_EXEMPT_MIN_GRADE == _marquee_min_grade()


def _marquee_min_grade() -> int:
    import importlib.util

    path = REPO.parent / "backend/src/services/keirin_marquee.py"
    spec = importlib.util.spec_from_file_location("tl_marquee", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.FILL_ALL_MIN_GRADE)


def test_daily_cap_does_not_touch_generation():
    """🔴 **上限は入稿だけに効き、`type_lab_picks` の生成には触らない。**

    上限で落ちた商品も行は作られ、`settle_type_lab_picks.py` が採点する
    （あちらは入稿を一切参照しない）。だから「上限を掛けていなければどうだったか」を
    後から測れる。2026-09-01 のユーザー要件「測定はするためデータは作成し、
    入稿取り消し扱いとする」がこれで満たされる。

    生成側（`build_type_lab_picks.py`）に上限が漏れると、その日の商品そのものが
    消えて**比較台が無くなる**ので、構造で止める。
    """
    build = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    for token in ("DAILY_CAP", "cap_budget", "daily_cap"):
        assert token not in build, f"生成側に上限が漏れている: {token}"

    settle = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    for token in ("netkeirin_submissions", "submission_skips", "DAILY_CAP"):
        assert token not in settle, f"採点側が入稿を見ている: {token}"


def test_cap_excludes_exempt_from_both_sides():
    """🔴 **枠外は分母からも枠の消費からも外す**（2026-09-01・ユーザー指定）。

    枠外（9車・決勝・準決勝・グレード3以上）は落とさないので、母数に入れると
    「枠外が多い日ほど普通のレースが削られる」という逆向きの効き方になる
    （9車が12件ある日で7車が5件ぶん余計に消えていた）。
    出る件数は「枠外ぜんぶ ＋ それ以外の半分」。
    """
    src = SUBMIT_PY.read_text(encoding="utf-8")
    # 分母から外す
    i = src.index("n_judged = sum(")
    assert "not _exempt(r)" in src[i:i + 300], "分母から枠外を外していない"
    # 枠も消費しない（上限の判定は枠外を除いた件数で見る）
    assert "n_capped >= cap_budget" in src, "枠外込みの件数で上限を見ている"
    j = src.index("n_capped += 1")
    assert "if not _exempt(row):" in src[j - 200:j], "枠外が枠を消費している"


def test_axis_gate_skips_are_recorded():
    """🔴 **軸信頼ゲートで落ちた分も記録する**（2026-09-01）。

    それまでは「毎日10件前後が見送り一覧を埋める」ことを嫌って件数だけ数えていたが、
    日次上限の分母が「その回に判定するレース数」になり、**軸信頼ゲート落ちは分母に
    入る**。内訳が画面から追えないと上限の妥当性を検証できない。

    ⚠️ ログには出さない（`quiet`）。毎日10件前後あり、出すと本当に見るべき見送り
       （並び未公開・ゲート落ち）が埋もれる。
    """
    from src.submission_skips import ALL_CODES, AXIS_GATE, label

    assert AXIS_GATE in ALL_CODES, "理由コードが正本に登録されていない"
    assert label(AXIS_GATE), "バッジのラベルが無い"
    assert len(label(AXIS_GATE)) <= 8, "ラベルは8文字以内（一覧の行に収める）"

    src = SUBMIT_PY.read_text(encoding="utf-8")
    i = src.index("if use_axis_gate and not _passes_axis_gate(r):")
    block = src[i:i + 900]
    assert "SKIP_AXIS_GATE" in block, "記録していない（件数だけ数えている）"
    # 判定したことにする（＝上限の分母に入る）
    assert block[block.index("return ("):].startswith("return (SKIP_AXIS_GATE"), \
        "記録コードを返していない"
    assert "True)" in block[block.index("return ("):block.index("return (") + 400], \
        "「判定した」を返していない（上限の分母から漏れる）"


def test_daily_cap_always_allows_at_least_one():
    """🔴 **判定対象が少ない回でも最低1件は出す。**

    `int(1 * 0.5)` は 0 なので、そのままだと候補があるのに1件も出ない回ができる。
    割合は「絞る」ためのもので「出さない」ためのものではない。
    2026-09-01 にこの退化をテストが捕まえた。
    """
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "max(1, int(n_judged" in src, "最低1件の下駄が無い"


def test_daily_cap_is_disabled_when_race_count_is_unavailable():
    """🔴 **判定対象が無いときは上限を掛けない。**

    上限は「その回に判定するレース数 × 割合」なので、0 として扱われると上限も 0 になり
    **1件も入稿されない**。ゲートの「判定できないものは通す」と倒す向きを揃える。
    2026-09-01 に実際にこの誤りを書き、テストが捕まえた。
    """
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "n_judged > 0" in src, "判定対象が 0 のときの分岐が無い"
    i = src.index("n_judged > 0")
    assert "上限は掛けません" in src[i:i + 1200], "0 のときに上限を無効化していない"


def test_daily_cap_is_bound_from_backend_source():
    """日次上限と順位付けを写経していない（backend の正本を読み込む）。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "_GATE.DAILY_CAP_RACE_FRACTION" in src, "上限を正本から読んでいない"
    assert "_GATE.daily_cap_exempt" in src, "除外の判定を正本から読んでいない"
    assert "_GATE.cap_priority" in src, "順位付けを正本から読んでいない"
    assert "= 0.5" not in src, "割合を写している"


def test_daily_cap_is_checked_just_before_submitting():
    """🔴 上限は **`submit_row` の直前**で見ること。

    先に引くと、締切超過・並び未取得などで**出せなかった行にも枠を食わせる**。
    その日の上限は「入稿できた件数」に対して効かなければ意味がない。
    """
    src = SUBMIT_PY.read_text(encoding="utf-8")
    cap = src.index("if cap_budget is not None and not _exempt(row)")
    sub = src.index("ok, msg = submit_row(")
    closed = src.index("SKIP_CLOSED,")
    assert closed < cap < sub, "上限の判定位置が違う（他の見送りの後・入稿の直前であること）"


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

#: 穴狙いアイコンを付けるプラン。**ここを増やすときは理由を書くこと。**
#: 🔴 看板枠 `{型}_sign` は6型ぶんまとめて穴狙い。**買い方で決めている**——
#:    「当たれば15万円」を狙って人気薄の順列だけを買う構成なので、型に関係なく
#:    買い目そのものが穴狙いにしか読めない（`A_ana` と同じ理屈）。
LONGSHOT_PLANS = {"F_pay", "A_ana"} | {f"{t}_sign" for t in "ABCDEF"}


def test_only_declared_plans_are_longshot():
    """穴狙いアイコンは `LONGSHOT_PLANS` だけ。**複数可**なので選定は要らない。

    🔴 **アイコンの表は入稿しうるプラン全体を覆うこと。** 9車の型F が売る
       `F_hit` が漏れると `.get(..., 既定)` で黙って既定へ落ちる。
    🔴 `F_hit` に穴狙いを付けていないのは 2026-08-30 のユーザー判断
       （「穴狙いのアイコンは現状のまま様子見」）。広げると効果の切り分けが
       さらに難しくなる。
    🔴 `A_ana`（2026-08-31）は**指数1位を1点も買わない**商品なので、
       買い目そのものが穴狙い。上の「様子見」は同じ型で hit/pay を分ける話で、
       こちらは別の商品。
    """
    from src.type_lab import SELLABLE_PLAN_KEYS
    from scripts.netkeirin_submit_type_lab import ACT_TYPE_BY_PLAN
    from src.netkeirin_client import ACT_TYPE_DEFAULT, ACT_TYPE_LONGSHOT

    # 🔴 **表は「入稿しうるプラン」を覆っていればよく、超過は許す**（2026-08-31）。
    #    看板枠 `{型}_sign` は6型ぶん定義してあるが、実際に売るのは
    #    `SIGNBOARD_TYPES` の型だけ。ダイヤルを回した瞬間に
    #    `.get(..., 既定)` で黙って既定アイコンへ落ちるのを防ぐため、
    #    表には最初から6型ぶん入れてある。**不足は許さない**ので検出力は落ちない。
    assert set(SELLABLE_PLAN_KEYS) <= set(ACT_TYPE_BY_PLAN), \
        "入稿しうるプランが表から漏れている"
    assert all(k in SELLABLE_PLAN_KEYS or k.endswith("_sign")
               for k in ACT_TYPE_BY_PLAN), "表に素性の分からないプランがある"
    assert {k for k, v in ACT_TYPE_BY_PLAN.items()
            if v == ACT_TYPE_LONGSHOT} == LONGSHOT_PLANS
    assert all(v == ACT_TYPE_DEFAULT for k, v in ACT_TYPE_BY_PLAN.items()
               if k not in LONGSHOT_PLANS)


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


@pytest.mark.parametrize("n_entries,race_type", [(7, "決勝"), (7, "チャレンジ決勝"),
                                                 (9, "決勝")])
def test_type_f_pay_is_longshot_regardless_of_car_count(n_entries, race_type):
    """🔴 `F_pay` を売るときのアイコンは**車数で変わらない**。

    ⚠️ 決勝以外は `F_hit` を売るのでこの表には含めない（別テスト）。
    商品の性格とも一致する——9車決勝の `F_pay` は表示的中 2.99%（67件中2件）・
    払戻中央 169,545円 で、この体系で最も極端な一撃枠。

    ⚠️ 将来 `ACT_TYPE_BY_PLAN` を車数別に持つと、ここが落ちる。
       落ちたときは「車数で売り方を変えた」という設計変更の合図として扱うこと。
    """
    from scripts.netkeirin_submit_type_lab import ACT_TYPE_BY_PLAN
    from src.netkeirin_client import ACT_TYPE_LONGSHOT

    plans = sell_plans_for("F", n_entries, race_type)
    # 🔴 7車は看板枠（`F_sign`）・9車決勝は `F_pay`。**どちらも穴狙いアイコン**という
    #    このテストの主張は変わらない（2026-09-01 に看板枠を再投入した）。
    assert [p.key for p in plans] == ["F_sign" if n_entries == 7 else "F_pay"]
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


def test_marquee_races_do_not_bypass_the_axis_gate():
    """🔴🔴 **看板レースにも軸信頼ゲートを掛ける**（2026-08-31・ユーザー判断 A案）。

    2026-08-28 は素通しさせていたが、その層がはっきり弱かった:

        看板でゲートを通る分   10.7件/日  表示的中 23.41%  ROI 88.8%
        看板でゲートに落ちる分  3.8件/日  表示的中 16.92%  ROI 68.2%

    掛けると全体 43.2→39.4件/日・表示的中 25.96→26.83%・ROI 82.4→83.8%（確認2026）。
    同数を無作為に落とす対照20本に**表示的中で両窓とも 20/20** で勝つ。

    🔴 代償は「看板レースに商品が出ない日がある」こと。ここが落ちたら
       2026-08-09 の「看板は必ず出す」方針へ戻したという合図なので、
       ユーザー判断を確かめてから直すこと。
    """
    from scripts.netkeirin_submit_type_lab import _passes_axis_gate

    weak = {"plan_key": "A_hit", "axis_sum": 1.0, "n_entries": 7,
            "race_type": "予選", "cup_grade": None}
    assert _passes_axis_gate(weak) is False
    assert _passes_axis_gate({**weak, "race_type": "決勝"}) is False
    assert _passes_axis_gate({**weak, "race_type": "特選"}) is False
    # 軸信頼が高ければ看板でも予選でも通る（落とすのは弱い層だけ）
    firm = {**weak, "axis_sum": 1.90}
    assert _passes_axis_gate(firm) is True
    assert _passes_axis_gate({**firm, "race_type": "決勝"}) is True


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
    # 🔴 判定は `_reject`（開始時の断面）と入稿ループ（この回の中）の**両方**で見る。
    #    2026-09-01 に2パスへ組み替えたとき、片方だけだと同じ実行の中で
    #    2つ目のプランが通る／既に取った行を再判定する、のどちらかが起きる。
    assert 'if rk in taken_by_type_lab:' in src, "_reject で見ていません"
    i = src.index("for row, rj in decided:")
    loop = src[i:i + 2500]
    assert "if race_key in taken_by_type_lab:" in loop, "入稿ループで見ていません"
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


# ───────────────── 軸信頼ゲートの ON/OFF（2026-08-31） ─────────────────

def test_axis_gate_enabled_falls_back_to_on(monkeypatch):
    """🔴 **読めなければ ON に倒す。**

    ゲートは「商品の定義」なので、設定が読めないことを理由に**外して**しまうと
    その日だけ落とすはずのレースを黙って売る。自動公開（読めなければ承認制＝
    公開しない側）とは倒す向きが逆であることを固定する。
    """
    import scripts.netkeirin_submit_type_lab as M

    class _Boom:
        def __enter__(self):
            raise RuntimeError("DB に届かない")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(M, "get_connection", lambda: _Boom())
    assert M.axis_gate_enabled() is True


def test_axis_gate_enabled_reads_the_global_row(monkeypatch):
    """`_global` 行の値をそのまま返す。行が無い・NULL のときも ON。"""
    import scripts.netkeirin_submit_type_lab as M

    def _conn(value):
        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                assert "netkeirin_settings" in sql and "_global" in str(params)

                class _R:
                    @staticmethod
                    def fetchone():
                        return None if value is _MISSING else {"axis_gate_enabled": value}
                return _R()
        return _C()

    for value, want in ((True, True), (False, False), (None, True), (_MISSING, True)):
        monkeypatch.setattr(M, "get_connection", lambda v=value: _conn(v))
        assert M.axis_gate_enabled() is want, value


_MISSING = object()


def test_the_gate_is_still_applied_by_default():
    """🔴 既定は ON。トグルを足しただけで挙動が変わっていないこと。"""
    from src.database import get_connection  # noqa: F401  (import できることの確認)
    import scripts.netkeirin_submit_type_lab as M
    src = (REPO / "scripts" / "netkeirin_submit_type_lab.py").read_text(encoding="utf-8")
    # ゲートの呼び出しはフラグと AND で結ばれていること（外し忘れの検出）。
    assert "if use_axis_gate and not _passes_axis_gate(r):" in src
    # フラグは run() の中で**1回だけ**読む（行ごとに DB を叩かない）。
    assert src.count("use_axis_gate = axis_gate_enabled()") == 1
    assert callable(M.axis_gate_enabled)


def _load_gate():
    import importlib.util

    path = REPO.parent / "backend/src/services/keirin_type_lab_gate.py"
    spec = importlib.util.spec_from_file_location("tl_gate_rp", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def test_rp_sd_quantiles_cover_every_7car_sellable_plan():
    """🔴 **7車で売るプランには必ず `rp_sd` の分位表がある。**

    無ければ `rp_sd_priority` が None を返し、そのプランだけ**軸信頼だけで並ぶ**。
    例外もログも出ないまま、新設プランが上限の順位付けから静かに外れる。

    ⚠️ `F_pay` は 2026-08-31 の看板枠導入で **9車決勝だけ**になった
    （7車の決勝・準決勝は `F_sign` へ回る）。9車は `_priority` が 1.0 を返して
    表を引かないので、ここでは対象外にする。
    """
    from src.type_lab import SELLABLE_PLAN_KEYS

    gate = _load_gate()
    nine_car_only = {"F_pay"}
    missing = sorted(k for k in SELLABLE_PLAN_KEYS
                     if k not in nine_car_only and k not in gate.RP_SD_PRIORITY_QUANTILES)
    assert not missing, (
        f"rp_sd の分位表が無いプラン: {missing}。"
        f"探索窓 {gate.AXIS_GATE_SOURCE_WINDOW} のプラン内分位を引いて足すこと")
    for k, qs in gate.RP_SD_PRIORITY_QUANTILES.items():
        assert len(qs) == 11, f"{k}: 分位の数が違う（0/10/…/100%の11点）"
        assert list(qs) == sorted(qs), f"{k}: 分位が単調でない"


def test_cap_priority_falls_back_to_axis_when_rp_sd_missing():
    """🔴 競走得点が読めないレースを中央へ寄せない（軸信頼だけの従来動作へ落ちる）。

    `axis_priority` の「知らなければ 0.5」と倒す向きが違うのは意図的。
    0.5 を返すと**欠測のレースだけ**が中央へ寄るという根拠のない偏りが入る。
    """
    gate = _load_gate()
    for plan in ("A_hit", "E_hit", "F_sign"):
        for a in (1.20, 1.44, 1.90):
            assert gate.cap_priority(plan, a, None) == gate.axis_priority(plan, a)
            # 🔴 NaN は「上端」ではなく「読めなかった」（`v < qs[i]` が全て偽になる）
            assert gate.cap_priority(plan, a, float("nan")) == gate.axis_priority(plan, a)


def test_cap_priority_weights_axis_twice(): 
    """合成の重みは 2:1（軸信頼:実力伯仲）。実測した腕と同じ形であること。"""
    gate = _load_gate()
    assert gate.RP_SD_PRIORITY_AXIS_WEIGHT == 2.0
    hi_axis_lo_rp = gate.cap_priority("A_hit", 1.90, 0.1)
    lo_axis_hi_rp = gate.cap_priority("A_hit", 1.00, 9.0)
    assert hi_axis_lo_rp > lo_axis_hi_rp, "軸信頼のほうが重いこと"


def test_submit_reads_race_point_once():
    """🔴 競走得点は**1回だけ**引く（行ごとに引くと当日90行ぶん DB を叩く）。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert src.count("_race_point_sd(") == 2, "定義と呼び出しが1組でない"
    assert src.index("rp_sd = _race_point_sd(") < src.index("def _priority(r: dict) -> float:")


def test_race_point_is_never_written_by_this_repo():
    """🔴 **`race_point` は読むだけ**（表示用の値で上書きした事故と同じ轍を踏まない）。

    `pred_*` と違って競走得点はスクレイパーが入れる生データで、`feature_wt` の
    `score_rank` / `score_z` の入力そのもの。ここへ書くと特徴量が自己参照で汚れる
    （2026-06〜07 に5週間汚染した実例がある）。だから入稿時に引き直してよい。
    """
    import re

    root = Path(__file__).resolve().parents[1]
    bad = []
    for f in sorted(root.glob("scripts/**/*.py")) + sorted(root.glob("src/**/*.py")):
        t = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"UPDATE\s+wt_entries\s+SET\s+([^\n]*)", t, re.I):
            if "race_point" in m.group(1):
                bad.append(f"{f.relative_to(root)}: {m.group(0)[:80]}")
    assert not bad, "race_point を書き換えている: " + "; ".join(bad)


# ─────────────── 欠測時の型は記録として信用できない ───────────────

def test_missing_lineup_skip_warns_that_the_type_is_not_trustworthy():
    """🔴 **欠測で見送った行の `rank_key` を型として読ませない**（2026-09-02）。

    並びが取れないと `arare` の加算5項のうち4項（ライン構成・ラインの維持・
    先頭の脚質・番手の得点）が全レース同値になり、荒れ度が実質「開催日目 − 2」
    だけの関数へ退化する。印が全車ゼロだと `axis_sum` も下がるので必ず混戦側へ
    落ち、**同じ回の欠測レースが全部同じ型**になる（2026-08-29 は18件すべて F、
    2026-08-31 は19件中18件が E、2026-09-01 は16件すべて F）。

    後の波で並びが取れると型はばらけるので（2026-09-01 は16件中12件が別の型）、
    見送りを型別に集計すると「特定の型だけ取りこぼしている」と読み違える。
    実際 2026-09-02 に読み違えた。理由の文言に警告を残して次の人を止める。
    """
    tree = ast.parse(SUBMIT_PY.read_text(encoding="utf-8"))
    texts: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        elts = node.value.elts
        if not elts or not (isinstance(elts[0], ast.Name)
                            and elts[0].id == "SKIP_MISSING_LINEUP"):
            continue
        # 理由の文言（f文字列・連結いずれでも中の定数を全部拾う）
        texts.append("".join(
            n.value for n in ast.walk(elts[1]) if isinstance(n, ast.Constant)
            and isinstance(n.value, str)))
    assert texts, "SKIP_MISSING_LINEUP を返す箇所が見つからない"
    for t in texts:
        assert "信用できない" in t, f"欠測時の型が信用できない旨が文言に無い: {t!r}"


def test_entry_health_records_the_type_degradation():
    """欠測時に型判定が退化することを `entry_health` が記録していること。

    ここが唯一の説明の置き場所。消えると同じ読み違えがまた起きる。
    """
    doc = (REPO / "src" / "entry_health.py").read_text(encoding="utf-8")
    for marker in ("開催日目", "arare", "集計は", "reason_code"):
        assert marker in doc, f"欠測時の型退化の記録に {marker} が無い"
