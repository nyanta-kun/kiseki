"""RANK_7H3（穴推奨・本命連対どまり型／三連単の高配当）の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件だけを選んである
（このリポジトリは入稿・採点経路が黙って壊れる事故を繰り返している）:

1. **母集団**: 看板・準決勝を1つも通さないこと。キーワードを写していないこと
2. **買い目**: フォーメーションの展開と `rank_7h3_build_legs()` が一致すること
   （食い違うと「送った買い目」と「記録した買い目」が別物になる）
3. **軸2車が必ず2着・3着に入る**こと（1着に置いたら別の商品になる）
4. **賭け金**: 合計が必ず予算ちょうどで、全点が最低単位以上
5. **ゲート**: 軸積の絶対閾値で切っていること（日次の相対順位ではない）
6. **入稿**: 候補JSON → 入稿行の変換で点数・金額・印が保たれること
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    RANK_7H3_AXIS_PRODUCT_MIN, RANK_7H3_BUDGET, RANK_7H3_LEG_P3_MIN, RANK_7H3_NE,
    RANK_7H3_UNIT, CURRENT_PAPER_RANKS, rank_7h3_axis, rank_7h3_axis_product,
    rank_7h3_build_legs, rank_7h3_daily_select, rank_7h3_formation,
    rank_7h3_is_target_race_type, rank_7h3_legs, rank_7h3_pl_prob, rank_7h3_stakes,
)


def _probs(**kw) -> dict[int, float]:
    """{車番: 3着内率}。7車ぶん埋める。"""
    base = {i: 0.05 for i in range(1, RANK_7H3_NE + 1)}
    base.update({int(k[1:]): v for k, v in kw.items()})
    return base


# 軸2車が強く、相手が 0.25 / 0.22 / 0.21 / 0.10 / 0.05 の標準形。
STD = {1: 0.90, 2: 0.85, 3: 0.25, 4: 0.22, 5: 0.21, 6: 0.10, 7: 0.05}


# ── 1. 母集団 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("race_type", [
    "決勝", "準決勝", "特選", "初特選", "選抜", "チャレンジ決勝", "ガールズ決勝",
    "チャレンジ準決勝", "チャレンジ選抜", "特秀",
])
def test_marquee_and_semifinal_are_excluded(race_type):
    """看板レースと準決勝は1つも通さない。

    🔴 準決勝も対象外。`marquee.is_marquee_type()` は準決勝を**看板ではない**と
       判定するので、あれを使うとここが通ってしまう。
    """
    assert rank_7h3_is_target_race_type(race_type) is False


@pytest.mark.parametrize("race_type", [
    "予選", "一般", "チャレンジ予選", "特予選", "特一般", "チャレンジ一般",
    "ガールズ予選(第１走)", "ガールズ一般", "男子新人戦予選１",
])
def test_non_marquee_race_types_are_targets(race_type):
    assert rank_7h3_is_target_race_type(race_type) is True


def test_keywords_are_not_redefined_here():
    """看板キーワードを 7H3 側で定義していないこと（二重管理の禁止）。

    正本は `backend/src/services/keirin_marquee.py`。写した瞬間に
    「看板の定義を変えたのに 7H3 だけ古い」を作れる。
    """
    src = (REPO / "src" / "strategy_wt.py").read_text(encoding="utf-8")
    section = src[src.index("RANK_7H3_NE ="):src.index("def rank_7h3_axis(")]
    for kw in ("決勝", "特選", "選抜", "特秀"):
        assert f'"{kw}"' not in section and f"'{kw}'" not in section, (
            f"看板キーワード {kw} を 7H3 側で定義している。正本から束縛すること")


# ── 2〜3. 買い目 ─────────────────────────────────────────────────────

def test_formation_expands_to_legs():
    """フォーメーションの展開と `build_legs()` が完全一致すること。

    🔴 netkeirin へ送るのはフォーメーション、picks_history に記録するのは
       展開後の買い目。ここがずれると**売った商品と記録した成績が別物**になる。
    """
    first, second, third = rank_7h3_formation(STD)
    expanded = {(t, a, b) for t in first for a in second for b in third
                if a != b and t not in (a, b)}
    legs = {tuple(int(x) for x in leg.split("-")) for leg in rank_7h3_build_legs(STD)}
    assert expanded == legs
    assert len(legs) == len(first) * 2       # 相手ごとに2点（軸2車の順序2通り）


def test_axis_always_takes_second_and_third():
    """軸2車は必ず2着・3着。**1着には絶対に置かない**。

    1着へ置いた形（a1-a2-相手）は看板外で edge 0.98＝歪みが無く、別の商品になる。
    """
    a1, a2 = rank_7h3_axis(STD)
    for leg in rank_7h3_build_legs(STD):
        first, second, third = (int(x) for x in leg.split("-"))
        assert {second, third} == {a1, a2}
        assert first not in (a1, a2)


def test_legs_apply_cut_then_strong1_weak2():
    """相手は「p3足切り → 強1＋弱2」。**素直な上位3車ではない**。

    強い1車が的中の主力、弱い2車が高配当の供給源。どちらかへ寄せると崩れる。
    """
    # 足切り(0.20)を5車とも通る形。上位3車なら [3,4,5] だが、正しくは 強1(3)+弱2(6,7)。
    wide = {1: 0.90, 2: 0.85, 3: 0.30, 4: 0.28, 5: 0.26, 6: 0.24, 7: 0.22}
    assert rank_7h3_legs(wide) == [3, 6, 7]
    # 足切りで3車だけ残る形（このときは結果的に上位3車と一致する）
    assert rank_7h3_legs(STD) == [3, 4, 5]


def test_legs_fall_back_when_cut_removes_everyone():
    """足切りで0車になったら3位・4位を使う（買い目が消えないこと）。"""
    weak = {1: 0.95, 2: 0.90, 3: 0.05, 4: 0.04, 5: 0.03, 6: 0.02, 7: 0.01}
    assert rank_7h3_legs(weak) == [3, 4]
    assert len(rank_7h3_build_legs(weak)) == 4


def test_leg_cut_threshold_is_absolute():
    """足切りは固定の絶対値（相対順位でも可変閾値でもない）。"""
    assert RANK_7H3_LEG_P3_MIN == 0.20
    just_under = {1: 0.90, 2: 0.85, 3: 0.199, 4: 0.199, 5: 0.199, 6: 0.1, 7: 0.05}
    assert rank_7h3_legs(just_under) == [3, 4]   # 全部足切り → フォールバック


# ── 4. 賭け金 ────────────────────────────────────────────────────────

def _win():
    return {1: 0.30, 2: 0.25, 3: 0.15, 4: 0.12, 5: 0.10, 6: 0.05, 7: 0.03}


def test_stakes_use_whole_budget_and_min_unit():
    legs = rank_7h3_build_legs(STD)
    stakes = rank_7h3_stakes(legs, _win())
    assert sorted(stakes) == sorted(legs)
    assert sum(stakes.values()) == RANK_7H3_BUDGET
    assert all(v >= RANK_7H3_UNIT and v % RANK_7H3_UNIT == 0 for v in stakes.values())


def test_stakes_fall_back_to_equal_without_win_probs():
    """1着率が無い/欠けるレースは均等へ落ちる（買い目は減らさない）。"""
    legs = rank_7h3_build_legs(STD)
    for wp in (None, {}, {1: 0.3, 2: 0.25}):     # 一部だけあるのも不可
        stakes = rank_7h3_stakes(legs, wp)
        assert sum(stakes.values()) == RANK_7H3_BUDGET
        # 予算が点数で割り切れないので完全な同額にはならない。差は最小単位1つぶんまで。
        assert max(stakes.values()) - min(stakes.values()) <= RANK_7H3_UNIT


def test_stakes_are_heavier_on_more_likely_points():
    """当たりやすい目に厚く置く（払戻を揃えるため）。"""
    legs = rank_7h3_build_legs(STD)
    stakes = rank_7h3_stakes(legs, _win())
    pl = {leg: rank_7h3_pl_prob(_win(), leg) for leg in legs}
    top = max(pl, key=lambda k: pl[k])
    bottom = min(pl, key=lambda k: pl[k])
    assert stakes[top] > stakes[bottom]


def test_pl_prob_is_a_probability_and_order_sensitive():
    p = rank_7h3_pl_prob(_win(), "3-1-2")
    q = rank_7h3_pl_prob(_win(), "3-2-1")
    assert 0 < p < 1 and 0 < q < 1
    assert p != q                                 # 着順で変わること
    assert rank_7h3_pl_prob(_win(), "3-1-9") is None   # 出走していない車


# ── 5. ゲート ────────────────────────────────────────────────────────

def _cand(**kw):
    c = {"n_entries": RANK_7H3_NE, "race_type": "予選",
         "axis_product": 0.80, "legs": ["3-1-2", "3-2-1", "5-1-2", "5-2-1"]}
    c.update(kw)
    return c


def test_daily_select_uses_absolute_axis_product_threshold():
    assert RANK_7H3_AXIS_PRODUCT_MIN == 0.70
    cands = [_cand(race_key="a", axis_product=0.71),
             _cand(race_key="b", axis_product=0.69)]
    picked = rank_7h3_daily_select(cands)
    assert [c["race_key"] for c in picked] == ["a"]


def test_daily_select_is_not_a_daily_rank_cut():
    """全部が閾値を超える日は全部通す（上位N件へ切り詰めない）。

    日次の相対順位で切ると件数が系統的に減る（7H1/9H1 で確認済みの型）。
    """
    cands = [_cand(race_key=str(i), axis_product=0.75 + i / 100) for i in range(9)]
    assert len(rank_7h3_daily_select(cands)) == 9


def test_daily_select_rejects_marquee_and_wrong_car_count():
    assert rank_7h3_daily_select([_cand(race_type="決勝")]) == []
    assert rank_7h3_daily_select([_cand(race_type="準決勝")]) == []
    assert rank_7h3_daily_select([_cand(n_entries=9)]) == []


def test_daily_select_rejects_too_few_legs():
    assert rank_7h3_daily_select([_cand(legs=["3-1-2"])]) == []
    assert rank_7h3_daily_select([_cand(legs=[])]) == []


def test_axis_product_matches_top2():
    assert rank_7h3_axis(STD) == (1, 2)
    assert rank_7h3_axis_product(STD) == pytest.approx(0.90 * 0.85)


# ── 6. 入稿 ──────────────────────────────────────────────────────────

def test_registered_in_paper_rank_registry():
    spec = next(s for s in CURRENT_PAPER_RANKS if s.rank == "RANK_7H3")
    assert (spec.suffix, spec.label) == ("#7H3", "7H3")
    assert spec.in_header_total is False      # 穴推奨系はヘッダー合計に混ぜない


def test_netkeirin_priority_is_below_7c():
    """入稿の優先順位で 7H3 が 7C より後ろにあること。

    7C（実質的中率39.0%）が的中体験を担い、7H3 は高配当担当。重複したレースを
    7H3 が先に取ると、表示的中5%の商品が的中体験を奪う。
    """
    from scripts.netkeirin_submit_wt import RANK_ORDER
    assert RANK_ORDER.index("7H3") > RANK_ORDER.index("7C")
    assert RANK_ORDER.index("7H3") < RANK_ORDER.index("7B")


def test_netkeirin_normalize_preserves_points_and_stakes():
    """候補JSON → 入稿行で点数・合計金額・印が保たれること。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7h3_candidate

    legs = rank_7h3_build_legs(STD)
    stakes = rank_7h3_stakes(legs, _win())
    cand = {"race_key": "20260812_11_01", "axis1": 1, "axis2": 2,
            "partners": rank_7h3_legs(STD), "legs": legs, "stakes": stakes}
    rows, marks, axis1, axis2 = _normalize_7h3_candidate(cand, RANK_CONFIGS["7H3"])
    assert len(rows) == len(legs)                       # 1点=1行
    assert sum(r.stake_per_line for r in rows) == RANK_7H3_BUDGET
    assert (axis1, axis2) == (1, 2)
    assert marks[1] == "◎" and marks[2] == "○"
    assert all(marks[p] == "△" for p in rank_7h3_legs(STD))


def test_netkeirin_normalize_rebuilds_stakes_when_json_is_stale():
    """候補JSONの stakes が買い目と食い違ったら**同じ関数で組み直す**こと。

    別式で埋めると記録側と入稿側が静かに食い違う（7H1 で実際に起きた型）。
    """
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7h3_candidate

    legs = rank_7h3_build_legs(STD)
    cand = {"race_key": "20260812_11_01", "axis1": 1, "axis2": 2,
            "partners": rank_7h3_legs(STD), "legs": legs,
            "stakes": {"9-9-9": 10000}}                 # 別レースの残骸を想定
    rows, _marks, _a1, _a2 = _normalize_7h3_candidate(cand, RANK_CONFIGS["7H3"])
    assert sum(r.stake_per_line for r in rows) == RANK_7H3_BUDGET


def test_netkeirin_normalize_rejects_degenerate_candidate():
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7h3_candidate

    with pytest.raises(ValueError):
        _normalize_7h3_candidate(
            {"axis1": 1, "axis2": 2, "legs": ["3-1-2"]}, RANK_CONFIGS["7H3"])
