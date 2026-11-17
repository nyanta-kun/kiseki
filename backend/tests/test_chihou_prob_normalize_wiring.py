"""地方の勝率・3着内率の読み出しがレース内で揃えた値を返すことの配線検査（Phase 1）。

正本 `src/indices/chihou_prob_normalize.py` を通らずに生の値を返す経路が戻ってきたら落ちる。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _func(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} に {name} が無い")


def _calls(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def test_race_indices_endpoint_normalizes() -> None:
    fn = _func(SRC / "api" / "chihou_races_router.py", "get_chihou_race_indices")
    assert "normalize_field" in _calls(fn)


def test_top_probability_sql_normalizes_within_race() -> None:
    fn = _func(SRC / "api" / "chihou_races_router.py", "get_chihou_top_probability")
    src = ast.unparse(fn)
    assert "SUM(win_probability) OVER (PARTITION BY race_id)" in src
    assert "NOT IN (1, 2)" in src


def test_recommender_inputs_normalize() -> None:
    path = SRC / "services" / "chihou_recommender.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    n_calls = sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "normalize_field"
    )
    # Routine 入力（get_today_chihou_race_data 系）と参考保持の推奨エンジンの2経路
    assert n_calls >= 2


def test_saved_values_stay_raw() -> None:
    """保存（calculated_indices・前向き記録）には正規化を掛けない（kiseki-lab が生値を読む）。"""
    for rel in ("indices/chihou_calculator.py", "services/chihou_place_pick_log.py"):
        text = (SRC / rel).read_text(encoding="utf-8")
        assert "chihou_prob_normalize" not in text, rel
