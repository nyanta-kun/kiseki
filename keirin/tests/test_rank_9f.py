"""RANK_9F（9車・看板穴埋めの成績記録）の不変条件（2026-08-25 新設）。

DB を触らない単体検査だけを置く。実データの整合は
`scripts/report_9car_full_coverage_wt.py` の重複チェックで見る。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _lines(spec: dict[int, tuple[int | None, int, int]]) -> dict[int, dict]:
    """{車番: (line_group, is_line_leader, line_size)} → `_axes` が期待する形。"""
    return {n: {"line_group": g, "is_line_leader": lead, "line_size": size}
            for n, (g, lead, size) in spec.items()}


def test_fill_axes_uses_canonical_rule():
    """軸は `submit_marquee_wt._axes()` に委ねる（写し取らない）。

    🔴 p3 上位2車をそのまま使うのは**本番ではない**。外れ群の ROI が
       63.5% ↔ 69.1% と別物になる（keirin/scripts/exp_9axis/axis_three_way.py）。
    """
    from scripts.backfill_9f_rank_wt import _fill_axes

    # 1 がライン先頭（3車ライン 1-2-3）・2位は別ラインの 4。
    # → 軸2は 4 ではなく 1 の同ライン最上位（2）へ組み替わる。
    p3 = {1: 0.70, 4: 0.60, 2: 0.50, 3: 0.30, 5: 0.25,
          6: 0.20, 7: 0.15, 8: 0.10, 9: 0.05}
    ln = _lines({1: (1, 1, 3), 2: (1, 0, 3), 3: (1, 0, 3),
                 4: (2, 1, 3), 5: (2, 0, 3), 6: (2, 0, 3),
                 7: (3, 1, 3), 8: (3, 0, 3), 9: (3, 0, 3)})
    assert _fill_axes(p3, ln) == (1, 2)


def test_fill_axes_keeps_top2_when_no_leader():
    """上位2車のどちらもライン先頭でなければ組み替えない。"""
    from scripts.backfill_9f_rank_wt import _fill_axes

    p3 = {2: 0.70, 5: 0.60, 1: 0.50, 3: 0.30, 4: 0.25,
          6: 0.20, 7: 0.15, 8: 0.10, 9: 0.05}
    ln = _lines({1: (1, 1, 3), 2: (1, 0, 3), 3: (1, 0, 3),
                 4: (2, 1, 3), 5: (2, 0, 3), 6: (2, 0, 3),
                 7: (3, 1, 3), 8: (3, 0, 3), 9: (3, 0, 3)})
    assert _fill_axes(p3, ln) == (2, 5)


def test_fill_legs_restores_to_minimum_instead_of_dropping():
    """穴埋めは**レースを落とせない**。足切りで下限を割ったら上位から戻す。

    🔴 ゲート通過側（`rank_9c_daily_select`）は逆に落とす。役割が違う。
    """
    from src.strategy_wt import RANK_9C_LEGS_MIN
    from scripts.backfill_9f_rank_wt import _fill_legs

    others = [3, 4, 5, 6, 7, 8, 9]
    p3 = {c: 0.01 for c in others}          # 全車が足切り(0.15)未満
    p3[3], p3[4] = 0.05, 0.04
    kept = _fill_legs(others, p3)
    assert len(kept) == RANK_9C_LEGS_MIN
    assert kept[:2] == [3, 4]               # 3着内率の高い順に戻す


def test_fill_legs_applies_cutoff_when_enough():
    from scripts.backfill_9f_rank_wt import _fill_legs

    others = [3, 4, 5, 6, 7, 8, 9]
    p3 = {3: 0.40, 4: 0.30, 5: 0.20, 6: 0.16, 7: 0.05, 8: 0.04, 9: 0.03}
    assert _fill_legs(others, p3) == [3, 4, 5, 6]


def test_9f_is_not_registered_as_paper_rank():
    """🔴 `CURRENT_PAPER_RANKS` へ登録しない（意図的）。

    登録すると kiseki Web の `_PAPER_RANK_LABELS` との機械照合
    （backend/tests/test_keirin_rank_consistency.py）が落ち、Web の集計へも混ざる。
    9F は**分析用の記録**なので rank 名だけで持つ。
    """
    from src.strategy_wt import CURRENT_PAPER_RANKS

    assert "RANK_9F" not in {s.rank for s in CURRENT_PAPER_RANKS}


def test_9f_registered_in_tail_reconcile():
    """9C と同じ夜に回さないと、同じレースに #9C と #9F が両方立つ。"""
    from tests.reconcile_spec import reconcile_specs

    assert reconcile_specs().get("9f") == "9F"
