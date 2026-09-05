"""ペーパーでも `pw_ent` が入ること（2026-09-05 の実バグ）。

🔴 `run_paper` が `cars` に `pw` を入れ忘れていたため、`rows_for_race` が
   `win_probs=None` で `race_shape` を呼び、**`pw_ent` が全行 0.0** になっていた。
   `pw_ent` は型A の売り分け（`A_ana`）の唯一の入力なので、ペーパーでは
   `A_ana` が一度も選ばれない＝**確認窓で採否を検証できない**状態だった。
   例外もログも出ないので、行を見るまで気付けない。

   実害: 2026-01〜08 のペーパー 2,431行が `pw_ent = 0`
   （2025年は `run_paper_vintage`→`run_live` 経由なので正常だった）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")


def _cars_assign(fn_name: str) -> ast.Assign:
    """関数の中の `cars = {...}` を返す。"""
    tree = ast.parse(_source())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "cars"):
            return node
    raise AssertionError(f"{fn_name} に cars = ... がありません")


@pytest.mark.parametrize("fn_name", ["run_paper", "run_live"])
def test_cars_carries_the_win_probability(fn_name):
    """🔴 **どの経路でも `cars` に `pw` を入れる。** 入れ忘れると pw_ent が 0 になる。"""
    src = ast.unparse(_cars_assign(fn_name))
    assert "pw=" in src, (
        f"{fn_name} が cars に pw を入れていません。"
        "pw_ent が全行 0 になり、A_ana が一度も選ばれなくなります")


def test_race_shape_gets_none_only_when_there_is_no_pw():
    """`rows_for_race` は pw が揃わないときだけ None を渡す（黙って 0 にしない）。"""
    from scripts.build_type_lab_picks import rows_for_race  # noqa: F401

    src = inspect.getsource(rows_for_race)
    assert 'v["pw"]' in src and "or None" in src, (
        "1着率の渡し方が変わっています。`pw_ent` の入り方を確認してください")


def test_pw_ent_is_zero_only_without_win_probs():
    """`race_shape` は win_probs が無いときだけ pw_ent=0（他の入力では 0 にならない）。"""
    from src.type_lab import race_shape

    cars = range(1, 8)
    common = (
        {c: 0.9 - 0.1 * c for c in cars},
        {c: 1 for c in cars}, {c: 1 for c in cars}, {c: "逃" for c in cars},
        {c: 100.0 for c in cars}, {c: 10.0 for c in cars}, 1,
    )
    assert race_shape(*common).pw_ent == 0.0
    got = race_shape(*common, {c: 0.5 - 0.05 * c for c in cars})
    assert got.pw_ent > 0.0, "1着率を渡しても pw_ent が入っていない"
