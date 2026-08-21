"""同着の当たり目判定を固定し、**keirin 側の実装と食い違わない**ことを見る。

`backend/src/services/keirin_result_top3.py` は `keirin/src/result_top3.py` と
同じ規則を持つ2つ目の実装（backend の Docker イメージに keirin/ が入らないため）。
片方だけ直すと静かに食い違うので、ここで両方に同じ入力を通して突き合わせる。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.services.keirin_result_top3 import (
    winning_combo_labels,
    winning_trifectas,
    winning_trios,
)

# (着順, 車番) の並び。3着同着・2着同着・1着同着・未確定を網羅する。
CASES = [
    [(1, 5), (2, 2), (3, 7)],                 # 通常
    [(1, 4), (2, 3), (3, 1), (3, 7)],         # 3着同着
    [(1, 4), (2, 3), (2, 5)],                 # 2着同着
    [(1, 1), (1, 2), (3, 5)],                 # 1着同着
    [(1, 1), (2, 2)],                         # 3車そろわない
    [],                                       # 空
    [(1, 1), (2, 2), (4, 3)],                 # 4着は3着以内ではない
]


def test_通常のレースは当たり目がひとつ():
    assert winning_trios([(1, 5), (2, 2), (3, 7)]) == [frozenset({2, 5, 7})]
    assert winning_trifectas([(1, 5), (2, 2), (3, 7)]) == [(5, 2, 7)]


def test_3着同着は三連複も三連単も2通り():
    fin = [(1, 4), (2, 3), (3, 1), (3, 7)]
    assert winning_trios(fin) == [frozenset({1, 3, 4}), frozenset({3, 4, 7})]
    assert winning_trifectas(fin) == [(4, 3, 1), (4, 3, 7)]


def test_1着同着は三連複1通り_三連単2通り():
    fin = [(1, 1), (1, 2), (3, 5)]
    assert winning_trios(fin) == [frozenset({1, 2, 5})]
    assert winning_trifectas(fin) == [(1, 2, 5), (2, 1, 5)]


def test_3車そろわなければ空():
    assert winning_combo_labels([(1, 1), (2, 2)]) == []
    assert winning_combo_labels([]) == []


def test_行の並び順に依らない():
    fin = [(1, 4), (2, 3), (3, 1), (3, 7)]
    assert winning_combo_labels(fin) == winning_combo_labels(list(reversed(fin)))


def test_買い目の表記へ揃う():
    """`bet_detail.lines[].combo` と直接比較できる形であること。"""
    labels = winning_combo_labels([(1, 4), (2, 3), (3, 1), (3, 7)])
    assert "3=4=7" in labels          # 三連複は車番昇順を = でつなぐ
    assert "4-3-7" in labels          # 三連単は着順を - でつなぐ
    assert "7=4=3" not in labels


def _keirin_module():
    """採点側の実装（`keirin/src/result_top3.py`）をパスで読み込む。"""
    path = Path(__file__).resolve().parents[2] / "keirin" / "src" / "result_top3.py"
    if not path.exists():
        pytest.skip(f"keirin 側の実装が無い: {path}")
    spec = importlib.util.spec_from_file_location("_keirin_result_top3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("fin", CASES)
def test_keirin側の実装と出力が一致する(fin):
    """🔴 実装が2つあるので、ここが食い違いの唯一の検出点。"""
    k = _keirin_module()
    assert winning_trios(fin) == k.winning_trios(fin), f"三連複が食い違う: {fin}"
    assert winning_trifectas(fin) == k.winning_trifectas(fin), f"三連単が食い違う: {fin}"
