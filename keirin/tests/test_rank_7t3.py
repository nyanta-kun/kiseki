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
