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
    AXIS_SUM_FIRM, BEHIND_MID, BUDGET, PLANS, allocate, build_legs,
    mean_expected_payout, min_expected_payout, plans_for, race_shape, rule_version,
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


def test_backfill_refuses_to_write_when_the_axis_disagrees():
    """🔴🔴 **軸が1行でも食い違ったら、その回は1行も書かない。**

    突き合わせられるのは `axis1`/`axis2` ＝ 並びの先頭2つだけ。
    先頭2つが合っていても3位以下は半分違う（2025-07-15 実測: 別ソースで復元した
    47行のうち 完全一致 24 / **先頭2つだけ一致 23**）。そして3位以下こそが
    「順当（3着が指数3〜4位）」と「軸2+穴（指数5〜7位）」を分ける当の情報なので、
    部分的に書くと答え合わせの土台が静かに壊れる。

    2026-08-27 に「先頭2つが合った行だけ書く」実装で 2025年ぶん 16,864 行を
    埋めてしまい、実測に気づいて全部 NULL へ戻した。
    """
    src = (REPO / "scripts" / "backfill_type_lab_outcome.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_apply_order")
    body = ast.unparse(fn)
    # 食い違いがあれば UPDATE へ進まずに抜けること
    assert "if miss:" in body, "軸不一致で打ち切る分岐が無い"
    idx_guard = body.index("if miss:")
    idx_update = body.index("UPDATE type_lab_picks SET p3_order")
    assert idx_guard < idx_update, "軸不一致の判定が UPDATE より後にある"
    assert "return False" in body


def test_backfill_predicts_a_whole_window_at_once():
    """バックフィルは vintage 窓ごとにまとめて予測する（1日ずつだと9時間かかる）。"""
    src = (REPO / "scripts" / "backfill_type_lab_outcome.py").read_text(encoding="utf-8")
    assert "day_to=hi" in src, "窓をまとめて渡していない"
    build = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "max_date=day_to or day" in build
