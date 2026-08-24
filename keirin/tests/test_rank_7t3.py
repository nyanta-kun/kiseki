"""RANK_7T3（三連単・決勝の中配当枠）の回帰テスト（2026-08-24 新設）。

設計書: `docs/rank_7t3_design.md`。ここで固定するのは、崩れると**黙って別の商品に
なる**5点:

  1. 母集団は **race_type の完全一致**（部分一致だと準決勝を、marquee キーワードだと
     特選・選抜まで拾う。特選・選抜は単独 ROI 71.9% ＝控除率の壁の下）
  2. 買い目は **予測オッズ30倍以上 × 位置別合成 PL の確率上位5点**
     （EV順ではない・`pw` 単独 PL でもない）
  3. **ライン条件を持たない**（判定の正本は 7T1 側の1箇所だけ）
  4. **日次上限を掛けない**
  5. 入稿の優先順位で **7T1 の直後**
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

import src.strategy_wt as sw
from src.strategy_wt import (
    RANK_7T3_BUDGET, RANK_7T3_LEGS, RANK_7T3_MIN_ODDS, RANK_7T3_NE,
    CURRENT_PAPER_RANKS, rank_7t3_blend_probs, rank_7t3_daily_select,
    rank_7t3_is_target_race_type, rank_7t3_select, rank_7t3_stakes,
)

REPO = Path(__file__).resolve().parent.parent

CARS = list(range(1, RANK_7T3_NE + 1))
PW = {1: .30, 2: .20, 3: .15, 4: .12, 5: .10, 6: .08, 7: .05}
P3 = {1: .70, 2: .60, 3: .50, 4: .45, 5: .40, 6: .20, 7: .15}


def _flat_board(odds: float) -> dict[tuple[int, int, int], float]:
    """全順列が一律 `odds` 倍の板。帯の切り方だけを見たいときに使う。"""
    return {t: odds for t in itertools.permutations(CARS, 3)}


def _cand(**kw):
    base = {"n_entries": RANK_7T3_NE, "race_type": "決勝",
            "legs": ["1-2-3"], "race_key": "20260813_11_01"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 母集団

@pytest.mark.parametrize("race_type", ["決勝", "チャレンジ決勝"])
def test_target_race_types(race_type):
    assert rank_7t3_is_target_race_type(race_type) is True


@pytest.mark.parametrize("race_type", ["準決勝", "S級準決勝", "特選", "選抜",
                                       "特秀", "S級決勝", "予選", "一般", None])
def test_non_target_race_types(race_type):
    """🔴 **完全一致**であること。

    「決勝」で部分一致すると準決勝と S級決勝 を拾い、`MARQUEE_KEYWORDS` を使うと
    特選・選抜まで入る（単独 ROI 71.9% ＝壁の下）。検証は完全一致の定義で行った。
    """
    assert rank_7t3_is_target_race_type(race_type) is False


def test_does_not_depend_on_marquee():
    """🔴 `marquee` の判定・キーワードを使っていないこと（AST で見る）。

    文字列一致で見ると docstring の説明で落ちる（7T1 で実際に踏んだ）。
    """
    import ast

    tree = ast.parse((REPO / "src" / "strategy_wt.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "rank_7t3_is_target_race_type")
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    assert not ({"is_marquee_type", "MARQUEE_KEYWORDS"} & names)


# ---------------------------------------------------------------- 買い目確率

def test_blend_pl_is_a_probability_distribution():
    p = rank_7t3_blend_probs(CARS, PW, P3)
    assert len(p) == 7 * 6 * 5
    assert abs(sum(p.values()) - 1.0) < 1e-9


def test_blend_pl_matches_the_verification_implementation():
    """🔴 **出荷実装 = 検証実装**であること。

    設計書の数字は `scripts/exp_7t3/tfprob.blend_pl` の出力。式を写し間違えると
    「検証した商品と違うものを売る」ことになる（このリポジトリで繰り返し起きた型）。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_tfprob", REPO / "scripts" / "exp_7t3" / "tfprob.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ours = rank_7t3_blend_probs(CARS, PW, P3)
    theirs = m.blend_pl(CARS, PW, P3, (1, .5, 0))
    assert max(abs(ours[k] - theirs[k]) for k in ours) < 1e-12


def test_blend_pl_differs_from_win_only_pl():
    """🔴 `odds_prediction_tf.pl_ordered`（`pw` 単独）とは**別物**。

    三連単の買い目確率を1着率だけで作るのが元の穴で、位置別に合成すると
    確認窓の top1 的中が 5.99% → 10.07% になる。同じ値になったら
    合成が効いていない（重みの取り違え）。
    """
    from src.odds_prediction_tf import pl_ordered

    ours = rank_7t3_blend_probs(CARS, PW, P3)
    win_only = pl_ordered(PW, CARS)
    assert max(abs(ours[k] - win_only[k]) for k in ours) > 1e-3


# ---------------------------------------------------------------- 買い目

def test_select_takes_probability_top_n_within_the_band():
    legs = rank_7t3_select(P3, PW, _flat_board(50.0))
    assert len(legs) == RANK_7T3_LEGS
    probs = rank_7t3_blend_probs(CARS, PW, P3)
    got = [tuple(int(x) for x in leg.split("-")) for leg in legs]
    best = [k for k, _ in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))]
    assert got == best[:RANK_7T3_LEGS]


def test_select_is_empty_when_nothing_reaches_the_band():
    """帯に届く目が無ければ買わない（安い目で埋めない）。"""
    assert rank_7t3_select(P3, PW, _flat_board(RANK_7T3_MIN_ODDS - 0.1)) == []


def test_select_band_boundary_is_inclusive():
    assert rank_7t3_select(P3, PW, _flat_board(RANK_7T3_MIN_ODDS)) != []


def test_select_is_not_ev_ordered():
    """🔴 **EV順（確率×予測オッズ）にしていない**こと。

    100〜300倍帯では EV 順が優れるが、30倍帯では確率順のほうが素直。
    EV 順は探索窓で予測オッズモデルが in-sample なので過大評価される。
    帯の中でオッズに差を付け、EV 順とは違う集合が返ることで確かめる。
    """
    probs = rank_7t3_blend_probs(CARS, PW, P3)
    ranked = [k for k, _ in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))]
    # 確率が低い目ほど高いオッズを付ける（EV 順なら下位が選ばれる板）
    board = {k: 30.0 + 500.0 * i for i, k in enumerate(ranked)}
    got = [tuple(int(x) for x in leg.split("-"))
           for leg in rank_7t3_select(P3, PW, board)]
    assert got == ranked[:RANK_7T3_LEGS]
    ev_top = sorted(board, key=lambda k: -probs[k] * board[k])[:RANK_7T3_LEGS]
    assert got != ev_top


def test_stakes_are_equal_and_sum_to_budget():
    """🔴 均等配分。30倍という足切りは「全点が等額」を前提に決めてある。"""
    legs = rank_7t3_select(P3, PW, _flat_board(50.0))
    stakes = rank_7t3_stakes(legs)
    assert sum(stakes.values()) == RANK_7T3_BUDGET
    assert len(set(stakes.values())) == 1
    assert set(stakes) == set(legs)


# ---------------------------------------------------------------- 選出

def test_daily_select_filters_population_only():
    assert len(rank_7t3_daily_select([_cand()])) == 1
    assert rank_7t3_daily_select([_cand(race_type="準決勝")]) == []
    assert rank_7t3_daily_select([_cand(n_entries=9)]) == []
    assert rank_7t3_daily_select([_cand(legs=[])]) == []


def test_daily_select_has_no_line_condition():
    """🔴 **ライン条件を持たない**（設計書 §8）。

    別/同ラインの判定は「p3上位2車」の取り方に敏感で、軸2を p3 の3番手に替えると
    **44.5%** のレースで反転する。判定を2箇所に持つと、モデルの微小な更新で
    商品が入れ替わる。優先順位（7T1 の直後）だけで棲み分ける。
    """
    both = rank_7t3_daily_select([_cand(is_cross_line=True),
                                  _cand(race_key="x", is_cross_line=False)])
    assert len(both) == 2
    src = (REPO / "src" / "strategy_wt.py").read_text("utf-8")
    body = src[src.index("def rank_7t3_daily_select("):]
    body = body[:body.index("\n    return elig") + 20]
    assert "cross_line" not in body, "7T3 がライン判定を持っている"


def test_daily_select_has_no_daily_cap():
    """🔴 日次上限を掛けないこと。

    決勝のみで 3.6件/日（7T1 に譲った後は 1.4件/日）と元から薄い。
    7T1 の上限5は ROI を1ミリも改善していなかった（件数 1/3 で ROI 同じ）。
    """
    cands = [_cand(race_key=f"20260813_11_{i:02d}") for i in range(1, 21)]
    assert len(rank_7t3_daily_select(cands)) == 20


# ---------------------------------------------------------------- 登録・入稿

def test_registered_in_paper_rank_registry():
    """🔴 忘れると月次/年次サマリーに一切出ない（過去に3回事故）。"""
    spec = next(s for s in CURRENT_PAPER_RANKS if s.rank == "RANK_7T3")
    assert (spec.suffix, spec.label) == ("#7T3", "7T3")
    assert spec.in_header_total is False
    assert spec.in_live_report is True


def test_shape_fallback_has_7t3():
    """無いとレース構造ラベルが空になる。"""
    from src.race_shape import SHAPE_FALLBACK

    assert SHAPE_FALLBACK.get("7T3")


def test_netkeirin_config_and_priority():
    """入稿設定と優先順位。**7T1 の直後**であること。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, RANK_ORDER

    cfg = RANK_CONFIGS["7T3"]
    assert cfg["n_cars"] == RANK_7T3_NE
    assert cfg["file_key"] == "s7t3"
    # 🔴 1点=1行。5点の1着が1車に揃うのは 7.0% しかなく、
    #    1着1車固定のフォーメーションでは 93% のレースを表現できない。
    assert cfg["formation_bet_7t1"] is True
    assert cfg["overlap_expected"] is True
    assert RANK_ORDER.index("7T3") == RANK_ORDER.index("7T1") + 1
    assert RANK_ORDER.index("7T3") < RANK_ORDER.index("7S")


def test_comment_does_not_promise_manshaken():
    """🔴 商品説明で万車券を謳わないこと。

    的中の払戻内訳は 1万円以上が 4.7%（年6件）・**3万円超は0件**。
    「週2〜3ヒット」と「万車券」は同時に成立しない。
    """
    from scripts.netkeirin_submit_wt import RANK_CONFIGS

    c = RANK_CONFIGS["7T3"]["default_comment"]
    for bad in ("万車券", "一撃", "大穴"):
        assert bad not in c, f"7T3 の文面が「{bad}」を謳っている"


# ---------------------------------------------------------------- 文面

def test_comment_does_not_claim_two_axes():
    """🔴 **7T3 で「二軸」と書かない**（2026-08-24 実測）。

    7T3 は軸を固定せず、予測オッズ30倍以上の帯から確率上位の決着順を5点採るだけ。
    2026年の決勝200Rで実測すると:

        5点すべてに共通して含まれる車  2車 49.0% / **1車 50.5%** / 0車 0.5%
        5点の1着に現れる車の種類      1種 10.0% / 2種 65.5% / 3種以上 24.5%

    ＝ **半数のレースには「二軸」と呼べる2車が存在しない**。共通本文をそのまま
    使うと、買い目の軸でない2車を「照らし出した二軸」として売ることになる。
    """
    from scripts.update_netkeirin_templates import COMMENT_TEMPLATES, TITLE_TEMPLATES

    body = COMMENT_TEMPLATES["7T3"]
    assert "二軸" not in body
    assert "{axis1}" not in body and "{axis2}" not in body
    assert "二軸" not in TITLE_TEMPLATES["7T3"]
    # 他ランクは従来どおり【二軸】節を持つ（落としたのは 7T3 だけ）
    assert "二軸" in COMMENT_TEMPLATES["7T1"]


def test_title_is_distinct_from_7t1():
    """🔴 7T1 と語を分ける。帯が違う（払戻中央 17.9万 ↔ 7.8万・的中 5% ↔ 10%）。"""
    from scripts.update_netkeirin_templates import TITLE_TEMPLATES

    assert TITLE_TEMPLATES["7T3"] != TITLE_TEMPLATES["7T1"]
    for banned in ("万車券", "一撃", "大穴", "自信", "本線"):
        assert banned not in TITLE_TEMPLATES["7T3"]


def test_shape_texts_do_not_claim_fixed_axes():
    """🔴 見解文で「固定」と書かない（7T1 と違い軸を置かない）。"""
    from src.race_shape import SHAPE_NOTES, SHAPE_TITLES

    for text in list(SHAPE_NOTES["7T3"].values()) + list(SHAPE_TITLES["7T3"].values()):
        for banned in ("固定", "万車券", "一撃", "堅い", "読み切"):
            assert banned not in text, f"7T3 の文面が「{banned}」を含む: {text}"


# ---------------------------------------------------------------- 印（◎○）

def test_axes_are_by_first_and_second_place_frequency():
    """🔴 ◎○ の決め方（2026-08-24 ユーザー判断）。

        ◎ = 買い目の**1着に最も多く現れる車**
        ○ = ◎を除いて、**1着または2着に最も多く現れる車**
    """
    from src.strategy_wt import rank_7t3_axes

    # 1着: 1が3回・2が1回・3が1回 → ◎=1
    # 1-2着（◎除く）: 2が3回・3が2回・… → ○=2
    legs = ["1-2-3", "1-2-4", "2-1-5", "3-1-2", "1-3-6"]
    assert rank_7t3_axes(legs) == (1, 2)


def test_axes_tie_break_prefers_the_higher_probability_leg():
    """⚠️ 同数なら **`legs` の並びが早いほう**（＝確率が高い買い目に出るほう）。

    車番昇順で切ると確率を無視した恣意的な選び方になる。
    `rank_7t3_select` は確率の降順で返すので、並び順がそのまま確率順。
    """
    from src.strategy_wt import rank_7t3_axes

    assert rank_7t3_axes(["1-2-3", "2-1-4"]) == (1, 2)
    assert rank_7t3_axes(["2-1-3", "1-2-4"]) == (2, 1)


def test_axes_are_empty_without_legs():
    from src.strategy_wt import rank_7t3_axes

    assert rank_7t3_axes([]) == (None, None)


def test_axes_are_marks_not_a_structural_claim():
    """🔴 印は付けるが、**文面では「二軸」と言わない**（2026-08-24 ユーザー判断）。

    印を付ける理由は「二軸探偵としてのブランドの一貫性」と「netkeirin が ◎○ を
    要求すること」であって、買い目が2車を軸に組まれているという主張ではない。

    実測（2026年の決勝200R）で ◎ は 5点すべてに含まれるのが **93.5%** と強いが、
    ○ は 52.0% で、◎○ が両方そろって全点に入るのは **46.5%** しかない。
    ＝ **2車で「二軸」と言い切れる形にはならない。**
    """
    from scripts.update_netkeirin_templates import COMMENT_TEMPLATES

    assert "二軸" not in COMMENT_TEMPLATES["7T3"]
    # ただし印そのものは出せること（関数が存在し、値を返す）
    from src.strategy_wt import rank_7t3_axes

    assert rank_7t3_axes(["1-2-3", "1-3-4", "1-2-5"]) == (1, 2)


def test_settings_row_is_created_disabled():
    """🔴 新ランクの行は **`enabled=false`** で作られること。

    `_is_enabled()` は fail-open（行が無いと常時ON）なので、新ランクは
    「行を先に `enabled=false` で入れる」運用。ところが
    `update_netkeirin_templates --apply` は行が無ければ `enabled=True` で
    INSERT するので、デプロイ直後にこちらが先に走ると**武装した状態で行が
    出来る**（後から手で INSERT すると主キー衝突で失敗し「入れたつもり」になる）。
    **実行順に頼らず落とす。**
    """
    from scripts.update_netkeirin_templates import NEW_RANKS_START_DISABLED

    assert "7T3" in NEW_RANKS_START_DISABLED
    src = (REPO / "scripts" / "update_netkeirin_templates.py").read_text("utf-8")
    assert "rank not in NEW_RANKS_START_DISABLED" in src, \
        "INSERT が enabled を固定値 True で入れている"


def test_bought_on_submit_includes_7t3():
    """🔴 7T3 は発走前の買い判定を持たない。足さないと **売っているのに
    Web の投資・回収サマリーから消える**（7T1 で 2026-08-15 に起きた事故）。"""
    from scripts.netkeirin_submit_wt import RANKS_BOUGHT_ON_SUBMIT

    assert "7T3" in RANKS_BOUGHT_ON_SUBMIT


def test_normalizer_derives_marks_when_axes_are_absent():
    """🔴 7T3 の候補JSONには `axis1`/`axis2` が無い。買い目から ◎○△ を導くこと。

    導かないと `_normalize_7t1_candidate` が KeyError になり、
    **全 7T3 レースが「候補情報不正」で無言スキップ**される。
    """
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7t1_candidate

    legs = ["1-2-3", "1-2-4", "1-3-5", "2-1-6", "1-4-7"]
    cand = {"race_key": "20260813_11_01", "legs": legs}
    rows, marks, axis1, axis2 = _normalize_7t1_candidate(cand, RANK_CONFIGS["7T3"])
    assert len(rows) == len(legs)
    assert (axis1, axis2) == (1, 2)             # 1着最多=1 / ◎除く1-2着最多=2
    assert marks[1] == "◎" and marks[2] == "○"
    # 買い目に出る残りは △、出ない車には印を付けない
    assert {c for c, m in marks.items() if m == "△"} == {3, 4, 5, 6, 7}


def test_equal_stake_trifecta_can_pull_forward():
    """🔴 7T1/7T3 は三連単でも**前倒しできる**こと（2026-08-24）。

    `_normalize_7t1_candidate` は「ダッチ配分は使わない」と明記されており
    賭け金は均等で板を一切見ないので、前倒しを止める理由（券種の形が変わる）が
    当てはまらない。

    ⚠️ **止めると 7T1/7T3 を優先順位の上へ置いた効果が消える。** 後の波の決勝を
       7T1 が最初に見て前倒しを見送ると `deferred_races` に入り、**下位の
       7S/7B/7C も朝に取れなくなる**＝売上が最も集まる決勝の朝の露出を失う。
    """
    import inspect

    from scripts.netkeirin_submit_wt import _can_pull_forward

    src = inspect.getsource(_can_pull_forward)
    assert "equal_stake_trifecta" in src
    assert "if is_trifecta and not equal_stake_trifecta:" in src
    # 呼び出し側が 7T1/7T3 を渡していること
    body = (REPO / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    assert "equal_stake_trifecta=is_7t1" in body
