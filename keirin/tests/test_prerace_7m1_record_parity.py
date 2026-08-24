"""発走前の 7M1 記録が**入稿と同じ買い目**であること（2026-08-24 新設）。

`notify_prerace_wt.judge_rank_7m1` は `picks_history` へ書く**記録側**。
相手の作り方（`strategy_wt.rank_7m1_select_legs`）は EV・予測オッズ・公式印を
見るが、この経路にはその3つが無い。したがって**ここで組み直してはいけない**
——引数なしで呼ぶと必ず旧・位置規則へ落ち、「入稿は EV 順 / 記録は位置規則」
という二重管理になる（2026-08-21 の EV 順導入時に実際そうなっていた）。

食い違うと Web の実績が実際に売った商品を説明しなくなる。
PR#289（賭け金の二重管理）と同じ型で3回目なので、ここで構造的に塞ぐ。
"""
from __future__ import annotations

import notify_prerace_wt as np_wt  # scripts/ は conftest で path 追加済


def _board_odds(cars=(1, 2, 3, 4, 5, 6, 7), odds=10.0):
    """7車の三連複盤面（全組み合わせに同じオッズ）。"""
    from itertools import combinations
    return {frozenset(c): odds for c in combinations(cars, 3)}


def _cand(legs, axis1=1, axis2=2, probs=None):
    return {
        "axis1": axis1, "axis2": axis2,
        "legs_7m1": list(legs),
        # 位置規則で組み直すと [5, 6, 7] になる並び。朝の買い目と必ず違う値にする。
        "top3_probs": probs or {1: .60, 2: .55, 3: .50, 4: .45,
                                5: .40, 6: .35, 7: .30},
    }


def test_uses_the_morning_legs_verbatim():
    """🔴 朝の `legs_7m1` をそのまま使う（盤面が7車そろっているとき）。

    位置規則で組み直すと [5, 6, 7] になる盤面で、朝の買い目 [3, 4, 6, 7] が
    そのまま記録されること。
    """
    decision, detail = np_wt.judge_rank_7m1(
        _cand([3, 4, 6, 7]), _board_odds())
    assert decision == "buy"
    assert detail["thirds"] == [3, 4, 6, 7]
    assert len(detail["combos"]) == 4


def test_records_the_deliberate_single_point():
    """🔴 ○1点への集中を**記録できる**こと。

    旧実装は `RANK_7M1_LEGS_MIN`(=2) を要求していたので、集中したレースだけ
    記録が丸ごと欠けた（＝Web の実績から一番濃い商品が消える）。
    """
    decision, detail = np_wt.judge_rank_7m1(_cand([4]), _board_odds())
    assert decision == "buy"
    assert detail["thirds"] == [4]
    assert len(detail["combos"]) == 1
    # 1点なら予算まるごと。均等割りの `unit_stake` でも 10,000円 になる。
    assert detail["stake"] == 10000


def test_does_not_rebuild_legs_when_morning_legs_exist():
    """朝の買い目があるときは `rank_7m1_select_legs` を呼ばないこと。

    呼んでしまうと（引数が無いので）位置規則の買い目に化ける。
    """
    called = []
    orig = np_wt.rank_7m1_select_legs
    np_wt.rank_7m1_select_legs = lambda *a, **k: called.append(a) or [9]
    try:
        _, detail = np_wt.judge_rank_7m1(_cand([3, 4, 6, 7]), _board_odds())
    finally:
        np_wt.rank_7m1_select_legs = orig
    assert not called, "朝の買い目があるのに組み直している"
    assert detail["thirds"] == [3, 4, 6, 7]


def test_falls_back_to_the_position_rule_only_without_morning_legs():
    """旧候補JSON（`legs_7m1` が無い）のときだけ盤面から組み直す。"""
    cand = _cand([])
    decision, detail = np_wt.judge_rank_7m1(cand, _board_odds())
    assert decision == "buy"
    # 位置規則: 相手5車(3..7)を p3 降順に並べた3番目から＝[5, 6, 7]
    assert detail["thirds"] == [5, 6, 7]


def test_skips_when_a_point_has_no_odds():
    """🔴 買い目の**全点**にオッズが要る。

    旧実装は2点あれば通したので「4点のうち3点しか記録しない」が起きた。
    記録は入稿と同じ買い目でなければ意味がない。
    """
    board = _board_odds()
    del board[frozenset({1, 2, 7})]          # 1点だけ欠かす
    decision, detail = np_wt.judge_rank_7m1(_cand([3, 4, 6, 7]), board)
    assert decision == "skip"
    assert "3/4点" in detail["skip_reason"]


def test_still_skips_on_a_scratched_board():
    """欠車（盤面が7車でない）は従来どおり見送り。"""
    decision, detail = np_wt.judge_rank_7m1(
        _cand([3, 4, 6]), _board_odds(cars=(1, 2, 3, 4, 5, 6)))
    assert decision == "skip"
    assert "欠車" in detail["skip_reason"]
