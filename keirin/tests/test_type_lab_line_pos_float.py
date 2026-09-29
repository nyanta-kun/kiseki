"""`race_shape` が隊列の位置を型を問わず同じに読むこと（2026-09-29 新設）。

比較台（`/tmp/race_type_board.npz`）の `A_line_pos` は float32。以前は
`str(line_pos) == "1"` で先頭・番手を探していたため、台から渡すと先頭が見つからず
**荒れ度（＝型ラベル）とラインの隊列順が静かにずれていた**（台の TYPE 列との一致 54%）。
本番の入力は integer なので影響は無かったが、検証（`lineup_sim.ctx` 経由の
product_redesign / axis_gate_redraw 等）が別の型で測っていた。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.type_lab import race_shape

CARS = list(range(1, 8))
P3 = {1: .80, 2: .70, 3: .45, 4: .40, 5: .30, 6: .20, 7: .15}
# 指数1位(1番)のラインは 3→1→2 の隊列。車番順と隊列順が違うので取り違えが見える。
LINE_GROUP = {1: "a", 2: "a", 3: "a", 4: "b", 5: "b", 6: "c", 7: "d"}
LINE_POS = {1: 2, 2: 3, 3: 1, 4: 1, 5: 2, 6: 1, 7: 1}
STYLE = {c: "追" for c in CARS}                  # 先頭が追い型 → 荒れ度 +2
RACE_POINT = {1: 99, 2: 80, 3: 90, 4: 70, 5: 60, 6: 50, 7: 40}   # 番手 > 先頭 → +1
BEHIND = {c: 10.0 for c in CARS}                # 先頭の遅れ率が低い → +1


def _shape(conv):
    lp = {c: conv(v) for c, v in LINE_POS.items()}
    return race_shape(P3, LINE_GROUP, lp, STYLE, RACE_POINT, BEHIND, 2)


BASE = _shape(int)


def test_baseline_finds_lead_and_second():
    """整数で渡したとき、先頭(3番)・番手(1番)の項が効いて隊列順になっている。"""
    assert BASE.lines[0] == (3, 1, 2)
    none = race_shape(P3, LINE_GROUP, {}, STYLE, RACE_POINT, BEHIND, 2)
    assert BASE.arare == none.arare + 4        # 追い型 +2 / 遅れ率 +1 / 番手の得点 +1


@pytest.mark.parametrize("conv", [str, float, np.float32, np.float64, np.int64,
                                  lambda v: f"{float(v)}"],
                         ids=["str", "float", "np.float32", "np.float64", "np.int64", "str_float"])
def test_same_shape_for_any_numeric_type(conv):
    got = _shape(conv)
    assert (got.type_label, got.arare, got.lines) == (BASE.type_label, BASE.arare, BASE.lines)


def test_missing_positions_are_not_read_as_lead():
    """欠測・0・NaN を先頭と読まない（欠測で荒れ度を動かさない）。"""
    for bad in (None, 0, float("nan"), "", "x"):
        lp = {c: bad for c in CARS}
        got = race_shape(P3, LINE_GROUP, lp, STYLE, RACE_POINT, BEHIND, 2)
        ref = race_shape(P3, LINE_GROUP, {}, STYLE, RACE_POINT, BEHIND, 2)
        assert (got.arare, got.lines) == (ref.arare, ref.lines)
