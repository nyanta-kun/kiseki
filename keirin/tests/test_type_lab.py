"""型ラボ（`src/type_lab.py`）の回帰テスト（2026-08-27 新設）。

ここで固定するのは、崩れると**検証した商品と違うものを見ることになる**点:

  1. 型判定（6層 → A〜F）と荒れ度の各項が効いていること
  2. 型ごとの買い方（`PLANS`）の形と点数
  3. 🔴 **順序の入れ替えを何点買うかは型で逆になる**（A は1順序・F は6順列）。
     設計の核心で、取り違えると確認窓 ROI が 79.2% → 66.8% になる
  4. 配分（ダッチは払戻が揃う / 信頼度傾斜は最低が床を下回らない）
  5. 🔴 **既存テーブルへ書かないこと**（全面置き換えの検証中なので隔離が前提）
  6. 手書きリストの一致（`PLANS` ↔ API の表示順）
"""
from __future__ import annotations

import ast
import itertools
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.type_lab import (  # noqa: E402
    AXIS_SUM_FIRM, BEHIND_MID, BUDGET, PLANS, UNIT, allocate, build_legs,
    mean_expected_payout, min_expected_payout, plans_for, race_shape, rule_version,
    sell_plans_for,
)

CARS = list(range(1, 8))


def _shape(p3=None, *, line_group=None, line_pos=None, style=None,
           race_point=None, behind=None, day=2):
    p3 = p3 or {1: .80, 2: .70, 3: .45, 4: .40, 5: .30, 6: .20, 7: .15}
    # 既定: 1-2-3 が3人ライン / 4-5 が2人 / 6,7 単騎
    line_group = line_group or {1: "a", 2: "a", 3: "a", 4: "b", 5: "b", 6: "c", 7: "d"}
    line_pos = line_pos or {1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 1, 7: 1}
    style = style or {c: "逃" if c in (1, 4) else "追" for c in CARS}
    race_point = race_point or {c: 100 - c for c in CARS}
    behind = behind or {c: 20.0 for c in CARS}
    return race_shape(p3, line_group, line_pos, style, race_point, behind, day)


# ─────────────────────────── 型判定 ───────────────────────────

def test_firm_and_mixed_split_at_the_shared_constant():
    """軸の堅さの境界は 1.44（7C/7M1 が共有する値と同じ）。"""
    firm = _shape({1: .80, 2: .70, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05})
    mixed = _shape({1: .60, 2: .50, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05})
    assert firm.axis_sum >= AXIS_SUM_FIRM and firm.firm
    assert mixed.axis_sum < AXIS_SUM_FIRM and not mixed.firm
    assert firm.type_label in "ABC" and mixed.type_label in "DEF"


def test_arare_terms_each_move_the_score():
    """荒れ度の5項が**それぞれ**効くこと。1つでも効かなくなったら型が壊れる。"""
    base = _shape()
    # ③ 指数1位が2人ラインなら +1（既定は3人ラインで 0）
    two = _shape(line_group={1: "a", 2: "a", 3: "b", 4: "b", 5: "b", 6: "c", 7: "d"},
                 line_pos={1: 1, 2: 2, 3: 1, 4: 2, 5: 3, 6: 1, 7: 1})
    assert two.arare == base.arare + 1
    # ④ 先頭の遅れ率が中央未満なら +1（自力の実績が無い）
    slow = _shape(behind={c: BEHIND_MID - 1 for c in CARS})
    assert slow.arare == base.arare + 2
    # ⑥a 先頭が追い型なら +2
    chase = _shape(style={c: "追" for c in CARS})
    assert chase.arare == base.arare + 2
    # ⑤ 開催日目
    assert _shape(day=3).arare == base.arare + 1
    assert _shape(day=1).arare == base.arare - 1
    # ⑥c 番手の競走得点が先頭より高いなら +1
    inv = _shape(race_point={1: 90, 2: 99, 3: 80, 4: 70, 5: 60, 6: 50, 7: 40})
    assert inv.arare == base.arare + 1


LOW_BEHIND = {c: BEHIND_MID - 1 for c in CARS}      # 先頭に自力の実績が無い → 荒れ度 +1
FIRM_P3 = {1: .80, 2: .70, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05}
MIXED_P3 = {1: .60, 2: .50, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05}


def test_all_six_types_are_reachable():
    seen = set()
    for p3 in (FIRM_P3, MIXED_P3):
        for behind in (None, LOW_BEHIND):
            for day in (1, 2, 3):
                seen.add(_shape(p3, behind=behind, day=day).type_label)
    assert seen == set("ABCDEF"), seen


def test_returns_none_when_probabilities_are_missing():
    assert race_shape({}, {}, {}, {}, {}, {}, 2) is None


# ─────────────────────────── 買い目 ───────────────────────────

def _tf_boards(order):
    """全順列に予測オッズと確率を付けた板。オッズは車番が大きいほど高くする。"""
    odds, prob = {}, {}
    for c in itertools.permutations(CARS, 3):
        o = 3.0 + sum(c)
        odds[c] = o
        prob[c] = 1.0 / o
    return odds, prob


def test_type_a_buys_one_order_only():
    """🔴 型A（鉄板）は着順まで読めるので **1順序だけ**。

    入れ替えを足すと確認窓でガミ 0.6→17.9%・払戻中央 21,930→14,740円・
    ROI 80.1→75.7% と一貫して悪化する（SUMMARY 追補 B）。
    """
    s = _shape({1: .80, 2: .70, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05}, day=1)
    assert s.type_label == "A"
    odds, prob = _tf_boards(s.order)
    legs = build_legs(s, PLANS["A_hit"], odds, prob)
    assert len(legs) == 3
    a1, a2 = s.order[0], s.order[1]
    assert all(l[0] == a1 and l[1] == a2 for l in legs)


def test_type_f_buys_all_six_orders():
    """🔴 型F（大混戦）は3車が当たっても順序が読めないので **6順列すべて**。

    確認窓で `12` 単独 ROI 66.8% → `all6` 79.2%・2倍+/日 0.99 → 2.26。
    """
    s = _shape(MIXED_P3, behind=LOW_BEHIND, day=3)
    assert s.type_label == "F"
    odds, prob = _tf_boards(s.order)
    legs = build_legs(s, PLANS["F_hit"], odds, prob)
    assert len(legs) == 12                      # 相手2車 × 6順列
    assert len({frozenset(l) for l in legs}) == 2
    for trio in {frozenset(l) for l in legs}:
        assert sum(1 for l in legs if frozenset(l) == trio) == 6


def test_axis1_second2_fixes_first_and_opens_second():
    """ユーザー提案の構造: 1着=軸1固定・2着を2車・3着流し（三連単でのみ別物）。"""
    s = _shape({1: .80, 2: .70, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05}, day=1)
    odds, prob = _tf_boards(s.order)
    legs = build_legs(s, PLANS["A_pay"], odds, prob)
    a1 = s.order[0]
    assert all(l[0] == a1 for l in legs)
    assert len({l[1] for l in legs}) == 2        # 2着が2車
    assert len(legs) == 6                        # 2車 × 相手3車


def test_type_d_drops_the_most_popular_partner():
    """型Dは軸2車＋相手4点で、**相手5車のうち最人気（予測オッズ最小）を外す**。"""
    s = _shape({1: .60, 2: .50, 3: .4, 4: .3, 5: .2, 6: .1, 7: .05}, day=1)
    assert s.type_label == "D"
    a1, a2 = s.order[0], s.order[1]
    rest = list(s.order[2:])
    odds = {frozenset({a1, a2, c}): 5.0 + i for i, c in enumerate(rest)}
    prob = {k: 1.0 / v for k, v in odds.items()}
    legs = build_legs(s, PLANS["D_hit"], odds, prob)
    assert len(legs) == 4
    fav = min(odds, key=lambda k: odds[k])
    assert fav not in legs, "最人気の相手が残っている"


def test_prob_top_respects_band_and_sigma():
    s = _shape(FIRM_P3, day=3)
    assert s.type_label == "B"
    odds, prob = _tf_boards(s.order)
    legs = build_legs(s, PLANS["B_hit"], odds, prob)
    assert legs and len(legs) <= PLANS["B_hit"].max_legs
    assert sum(1.0 / odds[l] for l in legs) <= PLANS["B_hit"].sigma_max + 1e-9
    # 帯（型C は予測20倍以上）
    c = _shape(FIRM_P3, behind=LOW_BEHIND, day=3)
    assert c.type_label == "C"
    legs_c = build_legs(c, PLANS["C_hit"], odds, prob)
    assert legs_c and all(odds[l] >= PLANS["C_hit"].min_odds for l in legs_c)


# ─────────────────────────── 配分 ───────────────────────────

def test_dutch_equalises_the_payout():
    """ダッチ（∝1/予測オッズ）はどの点が当たっても払戻がほぼ同額になる。"""
    legs = [(1, 2, 3), (1, 2, 4), (1, 2, 5)]
    odds = {legs[0]: 5.0, legs[1]: 10.0, legs[2]: 40.0}
    prob = {l: 1.0 / odds[l] for l in legs}
    st = allocate(legs, odds, prob, PLANS["B_hit"])
    pays = [st[l] * odds[l] for l in legs]
    assert max(pays) / min(pays) < 1.15
    assert sum(st.values()) == BUDGET


def test_confidence_tilt_keeps_a_floor_and_favours_the_likely():
    """信頼度傾斜: 一番期待していない点も**床（予測ベースで floor_mult 倍）**を割らず、
    自信のある点ほど厚くなる。"""
    legs = [(1, 2, 3), (1, 2, 4), (1, 2, 5)]
    odds = {legs[0]: 4.0, legs[1]: 12.0, legs[2]: 40.0}
    prob = {legs[0]: 0.30, legs[1]: 0.10, legs[2]: 0.02}
    plan = PLANS["A_hit"]
    st = allocate(legs, odds, prob, plan)
    assert sum(st.values()) == BUDGET
    assert min_expected_payout(st, odds) >= BUDGET * plan.floor_mult * 0.95
    # 確率の高い点ほど賭け金が大きい
    assert st[legs[0]] > st[legs[1]] > st[legs[2]]


def test_confidence_tilt_refuses_when_the_floor_does_not_fit():
    """`Σ(1/予測オッズ) > 1/floor_mult` なら組めない＝ None を返す（黙って薄めない）。"""
    legs = [(1, 2, 3), (1, 2, 4)]
    odds = {legs[0]: 1.2, legs[1]: 1.3}
    prob = {l: 0.5 for l in legs}
    assert allocate(legs, odds, prob, PLANS["A_hit"]) is None


def test_mean_expected_payout_matches_the_definition():
    legs = [(1, 2, 3), (1, 2, 4)]
    odds = {legs[0]: 5.0, legs[1]: 10.0}
    st = {legs[0]: 4000, legs[1]: 2000}
    assert mean_expected_payout(st, odds) == pytest.approx((4000 * 5 + 2000 * 10) / 2)


# ─────────────────────── 隔離と手書きリスト ───────────────────────

_FORBIDDEN = ("picks_history", "netkeirin_submissions", "submission_skips",
              "netkeirin_settings")


@pytest.mark.parametrize("script", ["build_type_lab_picks.py",
                                    "settle_type_lab_picks.py",
                                    "backfill_type_lab_outcome.py"])
def test_scripts_never_touch_existing_product_tables(script):
    """🔴 **型ラボは既存商品のテーブルへ触らない。**

    全面置き換えの検証中なので、書くのも読むのも `type_lab_picks` だけに閉じる。
    ここが破れると既存の一覧・統計・売上集計へ静かに混入する
    （`keirin_sold_source_of_truth_2026_08_25` で一度直した型）。
    """
    tree = ast.parse((REPO / "scripts" / script).read_text(encoding="utf-8"))
    # docstring と # コメントは説明のために既存テーブル名を書いてよい。
    # **実行されるコード**だけを見る。
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    body = ast.unparse(ast.fix_missing_locations(tree))
    for name in _FORBIDDEN:
        assert name not in body, f"{script} が {name} に触っている"


def test_plan_keys_match_the_api_display_order():
    """`PLANS` と API の表示順リストが一致すること（手書きリストの足し忘れ検出）。"""
    api = (REPO.parent / "backend" / "src" / "api" / "keirin_type_lab_router.py")
    tree = ast.parse(api.read_text(encoding="utf-8"))
    order = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "PLAN_ORDER"):
            order = [e.value for e in node.value.elts]
    assert order is not None, "PLAN_ORDER が見つからない"
    assert set(order) == set(PLANS), (set(PLANS) ^ set(order))


def test_rule_version_changes_with_the_plans():
    before = rule_version()
    plan = PLANS["A_hit"]
    PLANS["A_hit"] = plan.__class__(**{**plan.__dict__, "n_partners": plan.n_partners + 1})
    try:
        assert rule_version() != before
    finally:
        PLANS["A_hit"] = plan
    assert rule_version() == before


def test_every_type_has_at_least_one_plan():
    for t in "ABCDEF":
        assert plans_for(t), f"型{t} に買い方が無い"


def test_comparison_rank_order_matches_production():
    """🔴 API の `CURRENT_RANK_ORDER` を本番の入稿優先順位と一致させる。

    比較表は「1レースで実際に売られる1商品」と並べるためにこの順序を使う。
    ずれると**別のランクと比べた数字**を出してしまう（手書きリストの足し忘れ型）。
    """
    import re

    api = (REPO.parent / "backend" / "src" / "api" / "keirin_type_lab_router.py")
    tree = ast.parse(api.read_text(encoding="utf-8"))
    order = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "CURRENT_RANK_ORDER"):
            order = [e.value for e in node.value.elts]
    assert order is not None, "CURRENT_RANK_ORDER が見つからない"

    src = (REPO / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    # RANK_CONFIGS の定義順がそのまま優先順位（RANK_ORDER = list(RANK_CONFIGS)）
    body = src[src.index("RANK_CONFIGS"):src.index("RANK_ORDER = list(RANK_CONFIGS)")]
    keys = re.findall(r'^\s{4}"([0-9A-Z]+)":\s*\{', body, flags=re.M)
    assert keys, "RANK_CONFIGS のキーを読めない（netkeirin_submit_wt.py の形が変わった）"
    assert [k.replace("RANK_", "") for k in order] == keys, (
        f"優先順位がずれている\n  API: {order}\n  本番: {keys}")


def test_live_path_imports_resolve():
    """🔴 live 経路の import が実在すること。

    `run_live` の import は関数の中にあるので、モジュールを読むだけでは検証できない。
    実際に import して落ちないことを見る。ここが壊れると**当日のバッチだけが
    毎朝 ModuleNotFoundError で落ちる**（paper は動くので気づきにくい）。
    """
    from src import odds_prediction_tf  # noqa: F401
    from src.models.trainer import load_model  # noqa: F401
    from src.preprocessing.feature_wt import (  # noqa: F401
        build_features_wt, load_raw_data_wt, prepare_X,
    )


def test_daily_batch_settles_today_as_well():
    """🔴 日次バッチは**当日ぶんも採点する**こと。

    レースは一日中終わり続けるので、前日ぶんだけを採点していると
    その日の結果が翌朝まで画面に出ない（2026-08-27 に指摘を受けた）。
    随時反映は `type_lab_settle.sh` が担うが、日次側も当日を見ておく。
    """
    daily = (REPO / "scripts" / "type_lab_daily.sh").read_text(encoding="utf-8")
    assert 'settle_type_lab_picks.py --date "$YEST"' in daily
    assert 'settle_type_lab_picks.py --date "$TODAY"' in daily


def test_intraday_settle_covers_yesterday_too():
    """🔴 随時採点は**前日ぶんも流す**こと。

    ミッドナイトの最終レースは 23:20〜23:30 発走で、確定着順が入るのは
    日付が変わった後。当日ぶんだけを見ていると 00 時以降の実行は
    `date +%F` が翌日を指すため、その日の最後の数レースが翌朝 07:15 の
    日次バッチまで埋まらない（2026-08-27 に「型ラボだけ結果が古い」と
    指摘を受けた原因のひとつ）。
    """
    hourly = (REPO / "scripts" / "type_lab_settle.sh").read_text(encoding="utf-8")
    assert "settle_type_lab_picks.py" in hourly and "date +%F" in hourly
    assert 'settle_type_lab_picks.py --date "$YEST"' in hourly
    assert 'settle_type_lab_picks.py --date "$TODAY"' in hourly


# ─────────────────────── 答え合わせ用の列 ───────────────────────

def test_build_stores_the_index_order():
    """🔴 **行を作った時点の指数の並びを焼き付ける。**

    後から `wt_entries` を引き直して並べ直すことは**できない**。モデルが再学習
    されれば p3 が変わり、当時と違う並びになる（paper は vintage・live は当日の
    本番モデル）。並びが無いと「3着が指数3〜4位から出たか」を検証できず、
    型分けの答え合わせが成り立たない。
    """
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "p3_order" in src
    tree = ast.parse(src)
    cols = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Assign)
         and any(getattr(t, "id", "") == "COLS" for t in n.targets)), None)
    assert cols is not None, "COLS が見つからない"
    assert "p3_order" in ast.unparse(cols), "COLS に p3_order が無い（保存されない）"


def test_settle_stores_the_race_level_trifecta_odds():
    """🔴 決着の三連単オッズは**券種と的中に関係なく**入れる。

    `final_odds` は「買った目」の確定オッズで的中時しか入らないため、
    外れたレースの荒れ具合が分からず「荒れ度が配当を当てているか」を測れない。
    三連複プラン（D_hit）の行にも三連単の値を入れることで型どうしを比べられる。
    """
    src = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    assert "win_tf_odds" in src
    # 的中の有無で分岐させていないこと（`final_odds` と同じ扱いにしない）
    assert 'odds.get(t["race_key"], {}).get(("trifecta", tf))' in src


def test_backfill_refuses_the_range_when_the_source_looks_wrong():
    """🔴🔴 見るのは「行が合っているか」ではなく「**ソースが正しいか**」。

    突き合わせられるのは `axis1`/`axis2` ＝ 並びの先頭2つだけ。**違うソース**だと
    先頭2つが合った行でも3位以下は半分違う（2025-07-15 実測: 47行のうち
    完全一致 24 / **先頭2つだけ一致 23**）。3位以下こそが「順当（3着が指数3〜4位）」と
    「軸2+穴（指数5〜7位）」を分ける当の情報なので、違うソースの行を部分的に書くと
    答え合わせの土台が静かに壊れる。

    幸い食い違い率は桁で分かれる（正しいソース 0.00〜0.07% ↔ 違うソース 34%）ので、
    率で判定して超えたら**その範囲は1行も書かない**。

    2026-08-27 に「先頭2つが合った行だけ書く」実装で 2025年ぶん 16,864 行を
    埋めてしまい、実測に気づいて全部 NULL へ戻した。
    """
    from scripts.backfill_type_lab_outcome import AXIS_MISMATCH_LIMIT_PCT
    # 正しいソースの実測 0.07% と違うソースの 34% の**どちらからも十分離れている**こと
    assert 0.1 < AXIS_MISMATCH_LIMIT_PCT < 10.0

    src = (REPO / "scripts" / "backfill_type_lab_outcome.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_apply_order")
    body = ast.unparse(fn)
    assert "AXIS_MISMATCH_LIMIT_PCT" in body, "食い違い率で打ち切る分岐が無い"
    idx_guard = body.index("AXIS_MISMATCH_LIMIT_PCT")
    idx_update = body.index("UPDATE type_lab_picks SET p3_order")
    assert idx_guard < idx_update, "食い違い率の判定が UPDATE より後にある"
    assert "return False" in body


def test_backfill_predicts_a_whole_window_at_once():
    """バックフィルは vintage 窓ごとにまとめて予測する（1日ずつだと9時間かかる）。"""
    src = (REPO / "scripts" / "backfill_type_lab_outcome.py").read_text(encoding="utf-8")
    assert "day_to=hi" in src, "窓をまとめて渡していない"
    build = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "max_date=day_to or day" in build


# ──────────────────── 9車の実投入（2026-08-28） ────────────────────
#
# 🔴 ここが崩れると、**測っていない買い方を9車で売る**ことになる。
#    9車の全8プランは両窓とも壁の下（ROI 69.8% / 72.8%）で、型F を外して
#    はじめて 83.0% / 89.1% になる。決勝だけ F_hit を残すのが実投入の形。
#    実測: `keirin/docs/type_lab/carcount_2026_08_27.md`（2026-08-28 追記）

def test_nine_car_type_f_sells_pay_for_the_final_and_hit_otherwise():
    """9車の型F は**決勝は F_pay・それ以外は F_hit**（2026-08-30 ユーザー判断）。

    🔴 **「決勝以外は売らない」に戻さないこと。** 2026-08-30 に 9車開催の
       看板8件（選抜3・特選3・特秀2）が無商品になった。旧ランクの看板穴埋めも
       型ラボ全面移行で機能しておらず、「看板には必ず出す」方針に反していた。
    🔴 全部 `F_pay` にすると表示的中 6.07%・ROI 60.5% で壁の下。だから
       決勝以外は当たる回数を売る `F_hit` に替える（ROI が上がるという主張ではない）。
    🔴 **生成（`plans_for`）は絞らない。** 売る／売らないは `sell_plans_for` の責務。
       生成側で空にすると比較台が消え、売らなかった側の成績が事後に測れない。
    """
    assert [p.key for p in sell_plans_for("F", 9, "決勝")] == ["F_pay"]
    for rt in ("準決勝", "一予選", "二予選", "選抜", "特選", "特秀", "一般", "", None):
        assert [p.key for p in sell_plans_for("F", 9, rt)] == ["F_hit"], rt
    # 生成側は種別で落とさない（比較台を残す）
    assert [p.key for p in plans_for("F", 9, "準決勝")] == ["F_hit", "F_pay"]


def test_nine_car_final_match_is_exact_not_substring():
    """🔴 `"決勝" in race_type` は準決勝を拾う（CLAUDE.md の既知の罠）。

    決勝と準決勝で**売るプランが変わる**ので、部分一致に戻すと準決勝まで
    `F_pay`（9車では表示的中 2.99%）になる。
    """
    assert [p.key for p in sell_plans_for("F", 9, "準決勝")] == ["F_hit"]
    assert [p.key for p in sell_plans_for("F", 9, "決勝")] == ["F_pay"]


def test_nine_car_other_types_are_unchanged():
    """型A〜E は9車でも 7車と同じ買い方（絞るのは型F だけ）。"""
    for t in "ABCDE":
        for rt in ("決勝", "準決勝", "一予選", None):
            assert ([p.key for p in plans_for(t, 9, rt)]
                    == [p.key for p in plans_for(t)]), f"型{t} が9車で変わっている"


def test_seven_car_is_untouched_by_the_nine_car_rule():
    """🔴 7車の挙動を変えていないこと（実地検証が走っている最中の変更なので）。"""
    for t in "ABCDEF":
        base = [p.key for p in plans_for(t)]
        assert [p.key for p in plans_for(t, 7, "決勝")] == base
        assert [p.key for p in plans_for(t, 7, "準決勝")] == base
        assert [p.key for p in plans_for(t, 7, None)] == base


def test_rule_version_separates_car_counts():
    """🔴 7車の版は変えず、9車だけ別世代にする。

    7車 53,017行の版が動くと「規則が変わった」と誤読される（買い方は不変）。
    逆に9車を同じ版のままにすると、**全8プランで作った `paper9` の行**と
    決勝限定の `live9` が同じ世代に見えてしまう。
    """
    assert rule_version() == rule_version(7)
    assert rule_version(9) != rule_version(7)


def test_build_script_passes_car_count_and_race_type_to_plans_for():
    """🔴 `plans_for` を素で呼ぶと 9車でも全8プランが出る。

    規則は正本にあるのに**呼び出し側が引数を渡し忘れる**のがこの型の壊れ方で、
    例外も件数の急増も出ない（9車が 5.6件/日 → 17.6件/日 になるだけ）。
    """
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "plans_for"]
    assert calls, "build_type_lab_picks.py が plans_for を呼んでいない"
    for c in calls:
        assert len(c.args) >= 3, "plans_for に車数と種別を渡していない"


def test_build_script_stamps_the_car_specific_rule_version():
    """`rule_version()` を素で呼ぶと 9車の行が 7車の版で保存される。"""
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for c in (n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "rule_version"):
        assert c.args, "rule_version に車数を渡していない"


def test_daily_batch_builds_both_car_counts():
    """日次バッチが 7車と9車を**別々に**組むこと（`live` と `live9` に分かれる）。"""
    sh = (REPO / "scripts" / "type_lab_daily.sh").read_text(encoding="utf-8")
    build = [ln for ln in sh.splitlines() if "build_type_lab_picks.py" in ln]
    assert len(build) == 2, f"生成の呼び出しが2本ない: {build}"
    assert sum("--n-entries 9" in ln for ln in build) == 1
    assert sum("--n-entries" not in ln for ln in build) == 1


def test_daily_batch_does_not_let_nine_car_kill_the_settle():
    """🔴 9車が落ちても採点まで止めない（`set -e` で打ち切られるため）。

    9車は `data/models/odds_tf_n9.txt` が要るので、配布漏れで必ず落ちる日がある。
    その日の7車の採点を巻き添えにしてはいけない。
    """
    sh = (REPO / "scripts" / "type_lab_daily.sh").read_text(encoding="utf-8")
    # ⚠️ 生の文字列位置で比べないこと（冒頭のコメントに同じ語が出てくる）。
    cmds = [ln for ln in sh.splitlines() if not ln.lstrip().startswith("#")]
    nine = next(i for i, ln in enumerate(cmds)
                if "build_type_lab_picks.py" in ln and "--n-entries 9" in ln)
    assert cmds[nine].lstrip().startswith("if !"), "9車の生成が set -e から守られていない"
    settle = next(i for i, ln in enumerate(cmds) if "settle_type_lab_picks.py" in ln)
    assert nine < settle, "9車の生成が採点より後にある"


def test_nine_car_trifecta_odds_model_is_distributed():
    """🔴 9車の三連単モデルが VPS への配布リストにあること。

    無いと `predict_board` が例外を投げ、毎朝 9車ぶんだけが丸ごと消える
    （PR#349 と同じ型の事故）。
    """
    sh = (REPO / "scripts" / "sync_models_to_vps.sh").read_text(encoding="utf-8")
    assert '"odds_tf_n9.txt"' in sh
    assert '"odds_tf_meta.json"' in sh


# ──────────────── 監査で見つかったテストの穴（2026-08-28） ────────────────
#
# 2026-08-28 の全体レビューで、次の変異を注入しても既存テストが**1本も落ちなかった**。
# どれも「例外もログも出ずに商品が別物になる」型なので、経路を固定する。

def test_axis_sum_firm_matches_the_production_constant():
    """🔴 `AXIS_SUM_FIRM` は 7C の `RANK_7C_P3_SUM_MIN` と**同じ値**であること。

    既存テストは「型判定が `AXIS_SUM_FIRM` を境に割れるか」しか見ておらず、
    **定数自身と比べている**ので 1.44 → 1.30 に変えても通ってしまった。
    型ラボは本番から import せずハードコードしているので、片方だけ動くと
    静かに別の商品になる。

    ⚠️ **値は同じでも「量」は違う。** 7C は `_gate_p3_sum`（較正後の
       `p3_sum_top2_cal`）を 1.44 と比べ、型ラボは `lgbm_wt_eval` の**生の p3**
       を比べている。決勝・上位グレードは較正で 0.01〜0.034 下がるため、
       境界帯のレースが型ラボでは「堅い」側へ寄る（実測 paper 全体の 2.8%・
       決勝の 5.7%）。**どちらを使うかは未決**（`docs/type_lab/audit_2026_08_28.md`）。
    """
    import re
    src = (REPO / "src" / "strategy_wt.py").read_text(encoding="utf-8")
    m = re.search(r"^RANK_7C_P3_SUM_MIN\s*=\s*([0-9.]+)", src, flags=re.M)
    assert m, "RANK_7C_P3_SUM_MIN が見つからない"
    assert AXIS_SUM_FIRM == float(m.group(1)), (AXIS_SUM_FIRM, m.group(1))


def test_prob_top_takes_the_highest_probability_combos_first():
    """🔴 B/C/E は「**確率上位**から積む」。並びを逆にしても既存テストは通っていた。

    EV順・確率昇順にすると別の商品になる（SUMMARY §2.3: 型A の表示的中は
    確率順 35.2% ↔ EV順 8.5%）。
    """
    shape = _shape()
    # 予測オッズは全点同じにして、確率だけで順序が決まるようにする
    perms = [p for p in itertools.permutations(range(1, 8), 3)]
    pred = {p: 50.0 for p in perms}
    prob = {p: 0.0 for p in perms}
    want = [(1, 2, 3), (1, 2, 4), (1, 2, 5)]
    for i, k in enumerate(want):
        prob[k] = 0.9 - i * 0.1                      # 明確に上位3点
    plan = PLANS["C_hit"]
    legs = build_legs(shape, plan, pred, prob)
    assert legs is not None
    assert list(legs)[:3] == want, list(legs)[:3]


def test_prob_top_respects_the_odds_band():
    """帯（`min_odds`）より下の目を拾わないこと。"""
    shape = _shape()
    perms = [p for p in itertools.permutations(range(1, 8), 3)]
    pred = {p: 5.0 for p in perms}                   # 全点が E_hit の 30倍未満
    prob = {p: 0.01 for p in perms}
    assert build_legs(shape, PLANS["E_hit"], pred, prob) is None


def test_allocate_drops_legs_that_would_get_zero_yen():
    """🔴 賭け金 0 円の点を返さない（2026-08-28）。

    ダッチ配分では極端に高い予測オッズの点の取り分が 1 単位（100円）に満たず
    0 円になる。実測で **B_hit の 61.7%（3,052行）** がこれを含み、
    `pred_min_payout` の中央値が 0円、`pred_mean_payout` が設計の床（3万円）を
    下回っていた。
    """
    plan = PLANS["B_hit"]
    legs = [(1, 2, 3), (1, 2, 4), (1, 2, 5), (1, 2, 6)]
    pred = {(1, 2, 3): 6.0, (1, 2, 4): 10.0, (1, 2, 5): 20.0,
            (1, 2, 6): 4742.0}          # ← 4,742倍は 1単位に届かない
    prob = {c: 0.1 for c in legs}
    st = allocate(legs, pred, prob, plan)
    assert st is not None
    assert (1, 2, 6) not in st, "0円の点が残っている"
    assert all(v > 0 for v in st.values())
    assert sum(st.values()) == BUDGET, "落としたぶんが予算から漏れている"


def test_dropping_zero_legs_barely_moves_the_money():
    """⚠️ 商品の変更ではなく記録の是正。落としても他の点の配分はほぼ動かない。"""
    plan = PLANS["B_hit"]
    keep = [(1, 2, 3), (1, 2, 4), (1, 2, 5)]
    pred = {(1, 2, 3): 6.0, (1, 2, 4): 10.0, (1, 2, 5): 20.0}
    prob = {c: 0.1 for c in keep}
    before = allocate(keep, pred, prob, plan)
    after = allocate(keep + [(1, 2, 6)], {**pred, (1, 2, 6): 4742.0},
                     {**prob, (1, 2, 6): 0.0001}, plan)
    assert before is not None and after is not None
    for c in keep:
        assert abs(before[c] - after[c]) <= UNIT, (c, before[c], after[c])


def test_allocate_keeps_every_leg_when_all_are_funded():
    """0円が出ないときは1点も落とさない（点数を勝手に減らさない）。"""
    plan = PLANS["B_hit"]
    legs = [(1, 2, 3), (1, 2, 4), (1, 2, 5)]
    pred = {(1, 2, 3): 6.0, (1, 2, 4): 10.0, (1, 2, 5): 20.0}
    st = allocate(legs, pred, {c: 0.1 for c in legs}, plan)
    assert st is not None and set(st) == set(legs)


def test_conf_allocation_never_produces_zero_legs():
    """信頼度傾斜は床が 1単位以上を保証するので、そもそも 0 円が出ない。"""
    shape = _shape()
    pred = {(1, 2, c): 10.0 + c for c in (3, 4, 5)}
    st = allocate([(1, 2, 3), (1, 2, 4), (1, 2, 5)], pred,
                  {c: 0.1 for c in pred}, PLANS["A_hit"])
    assert st is not None and all(v > 0 for v in st.values())
    assert shape is not None


def test_build_script_records_the_funded_legs_only():
    """🔴 `rows_for_race` は `legs` ではなく `stakes` を見ること。

    `legs` のまま回すと**買っていない点を記録する**（`n_legs` も想定払戻もずれる）。
    """
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "legs = [c for c in legs if c in stakes]" in src


# ──────────────── 画面のダークモード（2026-08-28） ────────────────

def test_type_lab_page_has_no_dark_mode_contrast_holes():
    """🔴 ダークモードで**背景だけ `dark:` を持ち文字色が明色前提**の箇所を禁じる。

    スマホのダークモードで**プランのバッジ（`D_hit` 等）が読めなくなっていた**
    （`bg-indigo-100 dark:bg-indigo-900 … text-indigo-800` で、暗い背景に暗い文字）。

    ⚠️ この repo は逆向きの罠（「ダークモードは本文色がほぼ白で色指定の無い数値が
       消える」）を既に踏んでいる。**背景と文字は必ず対で `dark:` を持たせる**。
    """
    import re
    page = (REPO.parent / "frontend" / "src" / "app" / "keirin" / "type-lab"
            / "page.tsx").read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r'className=(?:"([^"]*)"|\{`([^`]*)`\})', page, re.S):
        cls = (m.group(1) or m.group(2) or "").replace("\n", " ")
        line = page[:m.start()].count("\n") + 1
        if "dark:bg-" not in cls:
            continue
        if re.search(r'(?<!dark:)\btext-[a-z]+-\d{2,3}\b', cls) and "dark:text-" not in cls:
            bad.append(f"L{line}: {cls.strip()[:100]}")
    assert not bad, "背景に dark: があるのに文字色に無い:\n" + "\n".join(bad)


def test_type_lab_page_dark_text_colors_have_dark_variants():
    """濃い文字色（600〜900）は暗い背景に沈むので `dark:` 版を必ず持つこと。

    カードの背景は `dark:bg-gray-900` なので、`text-emerald-700` などは
    そのままだと**ほぼ黒地に暗緑**になる。
    """
    import re
    page = (REPO.parent / "frontend" / "src" / "app" / "keirin" / "type-lab"
            / "page.tsx").read_text(encoding="utf-8")
    palette = ("emerald|amber|red|indigo|teal|orange|sky|rose|green|blue|purple")
    bad = []
    for m in re.finditer(r'"([^"\n]*text-(?:%s)-[6-9]\d\d[^"\n]*)"' % palette, page):
        cls = m.group(1)
        if "dark:text-" in cls:
            continue
        bad.append(f"L{page[:m.start()].count(chr(10)) + 1}: {cls.strip()[:100]}")
    assert not bad, "濃い文字色に dark: 版が無い:\n" + "\n".join(bad)


# ──────── 画面の手書きリスト（2026-08-28・監査 C-4） ────────
#
# 🔴 `page.tsx` の定数は正本の写しだが、**テストは API の `PLAN_ORDER` しか
#    固定していなかった**。プランを増やすと、まとめには出るのに
#    「組み合わせ」のチェックボックスから**静かに消える**（`PLAN_KEYS` は
#    `PLAN_NOTE` の key から作られるため）。

def _page_src() -> str:
    return (REPO.parent / "frontend" / "src" / "app" / "keirin" / "type-lab"
            / "page.tsx").read_text(encoding="utf-8")


def _ts_object_keys(src: str, name: str) -> list[str]:
    """`const NAME: Record<...> = { a: ..., b: ... }` の key を順に返す。

    ⚠️ **行頭だけを見てはいけない。** 画面は 1行に複数の key を書いている
       （`A: "鉄板", B: "堅い・中",`）。値の中のカンマを拾わないよう、
       文字列リテラルを先に落としてから key を探す。
    """
    import re
    m = re.search(r"const\s+%s[^=]*=\s*\{(.*?)\n\};" % re.escape(name), src, re.S)
    assert m, f"{name} が見つからない"
    body = re.sub(r'"(?:[^"\\]|\\.)*"', '""', m.group(1))     # 値を潰す
    return re.findall(r"(?:^|[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body, re.M)


def test_page_plan_note_covers_every_plan():
    """🔴 `PLAN_NOTE` が全プランを持つこと（`PLAN_KEYS` の素になっている）。

    足りないとそのプランが「組み合わせ」のチェックボックスから消える。
    """
    keys = _ts_object_keys(_page_src(), "PLAN_NOTE")
    assert set(keys) == set(PLANS), set(keys) ^ set(PLANS)


def test_page_plan_note_order_matches_the_api():
    """並びも API の `PLAN_ORDER` と揃える（画面ごとに順序が違うと読み違える）。"""
    import ast
    api = (REPO.parent / "backend" / "src" / "api"
           / "keirin_type_lab_router.py").read_text(encoding="utf-8")
    order = None
    for node in ast.walk(ast.parse(api)):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "PLAN_ORDER"):
            order = [e.value for e in node.value.elts]
    assert order == _ts_object_keys(_page_src(), "PLAN_NOTE")


def test_page_default_combo_is_one_plan_per_type():
    """組み合わせの初期値は**型ごとに1つ**（＝競合が起きない並び）。

    同じ型から2つ入れると、そのレースは競合で丸ごと除外され件数が黙って減る。
    """
    import re
    src = _page_src()
    m = re.search(r"const DEFAULT_COMBO = \[(.*?)\];", src, re.S)
    assert m, "DEFAULT_COMBO が見つからない"
    combo = re.findall(r'"([A-Za-z0-9_]+)"', m.group(1))
    assert set(combo) <= set(PLANS), set(combo) - set(PLANS)
    types = [PLANS[p].type_label for p in combo]
    assert len(types) == len(set(types)), f"同じ型が2つ入っている: {types}"
    assert set(types) == {p.type_label for p in PLANS.values()}, "型が欠けている"


def test_page_finish_labels_match_the_server():
    """決着クラスは**サーバーの `FINISH_CLASSES` と対**。片方だけ増やすと「—」になる。"""
    import re
    outc = (REPO.parent / "backend" / "src" / "services"
            / "keirin_type_lab_outcome.py").read_text(encoding="utf-8")
    m = re.search(r"FINISH_CLASSES[^=]*=\s*\[(.*?)\n\]", outc, re.S)
    assert m, "FINISH_CLASSES が見つからない"
    server = re.findall(r'"key":\s*"([a-z0-9_]+)"', m.group(1))
    page = _page_src()
    for name in ("FINISH_LABEL", "FINISH_TONE"):
        assert _ts_object_keys(page, name) == server, (name, server)


def test_page_type_names_cover_every_type():
    """`TYPE_NAME` が 6型すべてを持つこと（欠けるとバッジが型記号だけになる）。"""
    keys = _ts_object_keys(_page_src(), "TYPE_NAME")
    assert set(keys) == {p.type_label for p in PLANS.values()}


def test_confidence_tilt_floor_is_exact_not_approximate():
    """🔴 床は `ceil`（切り上げ）。`int`（切り捨て）へ変えると**床を割る**。

    既存の床テストは `>= BUDGET * floor_mult * 0.95` と 5% 緩めていたため、
    `ceil → int` の変異を見逃していた（2026-08-28 の監査 D）。
    床が実際に効く形（残りがほとんど無く、しかも確率が偏っていて
    余りが1点へ寄る）で、**緩みなし**で検査する。

    予測 11.0倍・8点なら floor は ceil(13000/1100) = **12単位**（切り捨てなら 11）。
    12単位 = 1,200円 → 想定払戻 13,200円 ≥ 予算×1.3 = 13,000円。
    11単位なら 12,100円で**床を割る**。
    """
    legs = [(1, 2, c) for c in range(3, 8)] + [(1, 3, c) for c in range(4, 7)]
    assert len(legs) == 8
    odds = {c: 11.0 for c in legs}
    # 余りが1点へ寄るように確率を極端に偏らせる（他の点は床のまま残る）
    prob = {c: (1.0 if i == 0 else 1e-6) for i, c in enumerate(legs)}
    plan = PLANS["A_hit"]
    st = allocate(legs, odds, prob, plan)
    assert st is not None
    assert sum(st.values()) == BUDGET
    assert min_expected_payout(st, odds) >= BUDGET * plan.floor_mult, (
        "床を割っている（`ceil` を `int` にしていないか）")


def test_rebuilding_a_settled_row_drops_the_old_settlement():
    """🔴 買い目を差し替えたら採点を捨てる（監査 B-2）。

    `ON CONFLICT DO UPDATE` が `legs` だけ差し替えて `settled_at`/`hit`/`payout` を
    残すと、**古い当たり外れが新しい買い目に付く**。しかも `settle` は
    `settled_at IS NULL` しか見ないので**永久に直らない**。
    RUNBOOK 手順1（台を作り直す → paper を再生成 → 採点）がまさにこの形。

    ⚠️ 同じ内容で流し直したときは**消さない**（何度流しても害がない性質を保つ）。
    """
    import build_type_lab_picks as B
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "SETTLE_COLS" in src
    for col in ("settled_at", "win_combo", "hit", "payout", "final_odds", "win_tf_odds"):
        assert col in B.SETTLE_COLS, col
    # 条件つき（legs が変わったときだけ）であること
    assert "IS DISTINCT FROM excluded.legs" in src


# ───────────────── 型A の3分割（2026-08-31） ─────────────────
#
# 検証: `docs/type_lab/type_a_upset_2026_08_31.md` §12
#   ① pw_ent 上位10% → A_ana（穴狙い）  ② 三連複2点が通る → A_trio  ③ 残り → A_hit

def test_win_entropy_is_scale_free():
    """1着率は 0-1 でも 0-100 でも同じ値になること（呼び出し側の単位事故を防ぐ）。"""
    from src.type_lab import win_entropy
    a = {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}
    b = {c: v * 100 for c, v in a.items()}
    assert abs(win_entropy(a) - win_entropy(b)) < 1e-12
    assert win_entropy({}) == 0.0 and win_entropy(None) == 0.0


def test_race_shape_without_win_probs_is_unchanged():
    """🔴 `win_probs` を渡さなくても**型判定は一切変わらない**こと。

    渡し忘れても商品が変わらない（`pw_ent` が 0 になり A_hit へ倒れるだけ）。
    """
    from src.type_lab import race_shape
    args = (
        {1: .80, 2: .70, 3: .40, 4: .35, 5: .30, 6: .25, 7: .20},
        {c: 1 for c in range(1, 8)}, {c: c for c in range(1, 8)},
        {c: "逃" for c in range(1, 8)}, {c: 100.0 for c in range(1, 8)},
        {c: 5.0 for c in range(1, 8)}, 1,
    )
    a = race_shape(*args)
    b = race_shape(*args, win_probs={c: 1.0 / 7 for c in range(1, 8)})
    assert a is not None and b is not None
    assert (a.type_label, a.axis_sum, a.arare, a.gap, a.order) == \
           (b.type_label, b.axis_sum, b.arare, b.gap, b.order)
    assert a.pw_ent == 0.0 and b.pw_ent > 0.0


def test_type_a_sells_exactly_one_plan_in_every_combination():
    """🔴 **1レース1商品**。型A が3つに割れても返るのは必ず1つ。"""
    from src.type_lab import ANA_PW_ENT_MIN, sell_plans_for
    for pw in (None, 0.0, ANA_PW_ENT_MIN - 1e-9, ANA_PW_ENT_MIN, 2.0):
        for trio in (None, True, False):
            for n in (7, 9):
                got = sell_plans_for("A", n, "予選", pw_ent=pw, trio_ok=trio)
                assert len(got) == 1, (pw, trio, n, [p.key for p in got])


def test_type_a_split_priority():
    """①穴狙い → ②三連複 → ③A_hit の順（同着なら穴狙いが勝つ）。"""
    from src.type_lab import ANA_PW_ENT_MIN, sell_plans_for
    k = lambda **kw: sell_plans_for("A", 7, "予選", **kw)[0].key   # noqa: E731
    assert k(pw_ent=ANA_PW_ENT_MIN, trio_ok=True) == "A_ana"
    assert k(pw_ent=ANA_PW_ENT_MIN, trio_ok=False) == "A_ana"
    assert k(pw_ent=ANA_PW_ENT_MIN - 1e-9, trio_ok=True) == "A_trio"
    assert k(pw_ent=ANA_PW_ENT_MIN - 1e-9, trio_ok=False) == "A_hit"
    # 🔴 分からないものは現行へ倒す
    assert k(pw_ent=None, trio_ok=True) == "A_trio"
    assert k(pw_ent=None, trio_ok=None) == "A_hit"
    # 🔴 9車には掛けない（閾値が7車の分位なので絶対値切りになる）
    assert sell_plans_for("A", 9, "予選", pw_ent=2.0, trio_ok=True)[0].key == "A_hit"


def test_ana_never_buys_the_first_axis():
    """🔴 穴狙いは**軸1を1点も買わない**（買うと商品が矛盾する）。"""
    import itertools
    from src.type_lab import PLANS, RaceShape, build_legs
    order = (3, 5, 1, 4, 2, 6, 7)
    shape = RaceShape("A", 1.5, -1, 0.05, True, order, 1.6)
    odds = {t: 50.0 for t in itertools.permutations(range(1, 8), 3)}
    prob = {t: 1.0 / (i + 1) for i, t in enumerate(itertools.permutations(range(1, 8), 3))}
    legs = build_legs(shape, PLANS["A_ana"], odds, prob)
    assert legs and len(legs) == 5
    assert all(order[0] not in c for c in legs), legs


def test_a_trio_is_two_points_on_the_two_axes():
    """A_trio は軸2車＋相手2車の三連複2点（順序を捨てるだけ）。"""
    import itertools
    from src.type_lab import PLANS, RaceShape, build_legs
    order = (3, 5, 1, 4, 2, 6, 7)
    shape = RaceShape("A", 1.5, -1, 0.05, True, order, 1.0)
    odds = {frozenset(c): 8.0 for c in itertools.combinations(range(1, 8), 3)}
    legs = build_legs(shape, PLANS["A_trio"], odds, {})
    assert legs == [frozenset({3, 5, 1}), frozenset({3, 5, 4})]
