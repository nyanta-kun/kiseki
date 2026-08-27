"""型ラボの答え合わせ（`services/keirin_type_lab_outcome.py`）の検査。

分類は表示の土台なので、**境界を全部固定する**。ここが静かにずれると
「分割は効いている」という読みだけが残って、根拠が失われる。
"""
from __future__ import annotations

from src.services.keirin_type_lab_outcome import (
    FINISH_CLASSES,
    PAYOUT_BANDS,
    build_outcome,
    finish_class,
    finishers,
    index_ranks,
    payout_band,
)

#: 指数順位 1位=3番 / 2位=1番 / 3位=5番 / 4位=7番 / 5位=2番 / 6位=4番 / 7位=6番
ORDER = "3-1-5-7-2-4-6"


def test_finishers_accepts_both_bet_types():
    assert finishers("3-1-5") == (3, 1, 5)
    assert finishers("1=3=5") == (1, 3, 5)
    assert finishers(None) == ()
    assert finishers("3-1") == ()          # 3車そろわない入力は捨てる


def test_index_ranks():
    assert index_ranks(ORDER) == {3: 1, 1: 2, 5: 3, 7: 4, 2: 5, 4: 6, 6: 7}
    assert index_ranks(None) == {}


def test_finish_class_covers_every_case():
    # 軸2車（3番=1位・1番=2位）そろい + 3着が指数3位(5番) → 順当
    assert finish_class("3-1-5", ORDER) == "firm34"
    # 軸2車そろい + 指数4位(7番) も「順当」（3〜4位まで）
    assert finish_class("1-3-7", ORDER) == "firm34"
    # 軸2車そろい + 指数5位(2番) → 穴
    assert finish_class("3-1-2", ORDER) == "firm_ana"
    # 軸1車(3番) + 指数3位(5番)・4位(7番) → 片軸+中位
    assert finish_class("3-5-7", ORDER) == "half34"
    # 軸1車(1番) + 指数3位(5番)・6位(4番) → 片軸+穴
    assert finish_class("1-5-4", ORDER) == "half_ana"
    # 軸なし → 崩壊
    assert finish_class("5-7-2", ORDER) == "broken"


def test_finish_class_is_order_free():
    """並べ替えても同じクラス（分類に使うのは**集合**だけ）。"""
    assert finish_class("5-3-1", ORDER) == finish_class("3-1-5", ORDER) == "firm34"
    assert finish_class("1=3=5", ORDER) == "firm34"


def test_finish_class_needs_p3_order():
    """🔴 `p3_order` が無い行は**分類しない**。

    後から `wt_entries` を引き直して並べ直すと、モデルの再学習ぶんだけ
    当時と違う並びになる。「それらしい分類」を返す方が危険。
    """
    assert finish_class("3-1-5", None) is None
    assert finish_class("3-1-5", "") is None
    assert finish_class(None, ORDER) is None
    # 出走表に無い車番（8番）は分類できない
    assert finish_class("3-1-8", ORDER) is None


def test_payout_band_boundaries():
    assert payout_band(9.9) == "lt10"
    assert payout_band(10.0) == "10_30"      # 下限ちょうどは上の帯
    assert payout_band(29.9) == "10_30"
    assert payout_band(30.0) == "30_100"
    assert payout_band(99.9) == "30_100"
    assert payout_band(100.0) == "100_300"
    assert payout_band(299.9) == "100_300"
    assert payout_band(300.0) == "ge300"
    assert payout_band(9999.0) == "ge300"
    assert payout_band(None) is None
    assert payout_band(0) is None


def _pick(race_key: str, plan: str, type_label: str, win: str, *,
          p3_order: str | None = ORDER, odds: float | None = 25.0,
          hit: bool = False, gap: float = 0.2, settled: bool = True) -> dict:
    return {
        "race_key": race_key, "plan_key": plan, "type_label": type_label,
        "gap": gap, "settled_at": "2026-08-27" if settled else None,
        "hit": hit, "win_combo": win, "p3_order": p3_order, "win_tf_odds": odds,
    }


def test_build_outcome_dedupes_races():
    """🔴 1レースに2プラン当たる型（A・F）を**二重に数えない**。

    落とさないとその型だけ件数が倍になり、型ごとの分布が壊れる。
    """
    rows = [
        _pick("R1", "A_hit", "A", "3-1-5"),
        _pick("R1", "A_pay", "A", "3-1-5"),
        _pick("R2", "B_hit", "B", "5-7-2"),
    ]
    out = build_outcome(rows)
    assert out["n_races_settled"] == 2
    assert out["n_races"] == 2
    m = next(x for x in out["matrices"] if x["key"] == "type_finish")
    assert m["total"]["n"] == 2
    # プラン別の表は行ごとなので 3 行ぶん残る
    plan = next(x for x in out["matrices"] if x["key"] == "plan_finish")
    assert sum(r["n"] for r in plan["rows"]) == 3


def test_build_outcome_skips_unsettled_and_counts_unclassified():
    rows = [
        _pick("R1", "A_hit", "A", "3-1-5"),
        _pick("R2", "A_hit", "A", "3-1-5", p3_order=None),      # 分類できない
        _pick("R3", "A_hit", "A", "3-1-5", settled=False),      # 未採点
    ]
    out = build_outcome(rows)
    assert out["n_races_settled"] == 2
    assert out["n_races"] == 1
    assert out["n_unclassified"] == 1


def test_build_outcome_plan_cells_carry_hit_rate():
    rows = [
        _pick("R1", "A_hit", "A", "3-1-5", hit=True),
        _pick("R2", "A_hit", "A", "1-3-7", hit=False),
        _pick("R3", "A_hit", "A", "5-7-2", hit=False),
    ]
    out = build_outcome(rows)
    plan = next(x for x in out["matrices"] if x["key"] == "plan_finish")
    row = next(r for r in plan["rows"] if r["key"] == "A_hit")
    firm = next(c for c in row["cells"] if c["key"] == "firm34")
    assert firm["n"] == 2 and firm["n_hit"] == 1 and firm["hit_rate"] == 50.0
    broken = next(c for c in row["cells"] if c["key"] == "broken")
    assert broken["n"] == 1 and broken["n_hit"] == 0 and broken["hit_rate"] == 0.0


def test_build_outcome_payout_matrix_uses_win_tf_odds():
    """🔴 三連複プランの行でも**三連単のオッズ**で帯に入れる（型どうしを比べるため）。"""
    rows = [
        _pick("R1", "D_hit", "D", "1=3=5", odds=45.0),
        _pick("R2", "A_hit", "A", "3-1-5", odds=5.0),
        _pick("R3", "A_hit", "A", "3-1-5", odds=None),          # 帯に入らない
    ]
    out = build_outcome(rows)
    assert out["n_no_payout"] == 1
    m = next(x for x in out["matrices"] if x["key"] == "type_payout")
    d = next(r for r in m["rows"] if r["key"] == "D")
    assert next(c for c in d["cells"] if c["key"] == "30_100")["n"] == 1
    a = next(r for r in m["rows"] if r["key"] == "A")
    assert next(c for c in a["cells"] if c["key"] == "lt10")["n"] == 1
    assert a["median_tf_odds"] == 5.0


def test_gap_matrix_needs_enough_races():
    """レースが少ないときは相手の開きの表を**出さない**（3分位が意味を持たない）。"""
    out = build_outcome([_pick(f"R{i}", "A_hit", "A", "3-1-5") for i in range(10)])
    assert not any(m["key"] == "gap_finish" for m in out["matrices"])
    many = [_pick(f"R{i}", "A_hit", "A", "3-1-5", gap=i / 100) for i in range(60)]
    assert any(m["key"] == "gap_finish" for m in build_outcome(many)["matrices"])


def test_columns_are_stable():
    """列の key は画面と検証スクリプトが直に参照する。増減は意図的にだけ。"""
    assert [c["key"] for c in FINISH_CLASSES] == [
        "firm34", "firm_ana", "half34", "half_ana", "broken"]
    assert [b["key"] for b in PAYOUT_BANDS] == [
        "lt10", "10_30", "30_100", "100_300", "ge300"]
