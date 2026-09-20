"""「売った商品」の母集団に未送信の入稿案（proposed）が混ざらないことを固定する。

2026-09-20 の監査で、`deleted_at IS NULL` だけで絞っていた集計に
`status='proposed'`（承認待ち・netkeirin へ未送信）が混入していた
（採点済み 2,118 行中 15 行）。例外も出ず数字も自然に見えるので、SQL の形で固定する。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTER = ROOT / "backend/src/api/keirin_router.py"
TYPE_LAB_ROUTER = ROOT / "backend/src/api/keirin_type_lab_router.py"
REPORT = ROOT / "keirin/scripts/sold_performance_report.py"


def _func_source(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} が {path} に無い")


def test_settled_submissions_default_excludes_proposed() -> None:
    """既定（実績の集計）は submitted / published だけを数える。"""
    body = _func_source(ROUTER, "_fetch_settled_submissions")
    assert "include_proposed: bool = False" in body
    assert "('submitted', 'published')" in body
    assert "ns.status IN" in body


def test_only_review_screen_includes_proposed() -> None:
    """proposed を含めてよいのはレビュー画面のカードだけ。"""
    src = ROUTER.read_text(encoding="utf-8")
    assert len(re.findall(r"include_proposed=True", src)) == 1


def test_type_lab_sold_sql_excludes_proposed() -> None:
    """型ラボ画面の「売った」判定も送ったものだけ。"""
    src = TYPE_LAB_ROUTER.read_text(encoding="utf-8")
    sold = src.split("_SQL_SOLD = text(", 1)[1].split('""")', 1)[0]
    assert "status IN ('submitted', 'published')" in sold
    assert "status <> 'deleted'" not in sold


def test_sold_performance_report_excludes_proposed() -> None:
    """keirin 側の売上実績レポートも同じ母集団。"""
    src = REPORT.read_text(encoding="utf-8")
    assert "ns.status IN ('submitted', 'published')" in src
