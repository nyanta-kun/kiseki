"""分析スクリプトが指数バージョンを固定していないことを機械的に守る。

🔴 **バージョンのハードコードは例外にならない。** 古い version の行を静かに
読むだけなので、レポートは出るし数字も自然に見える。2026-09-01 まで
`chihou_cutoff_venue_review.py` の SQL が `ci.version = 13` を直書きしており、
v14 を 2026-08-14 にデプロイした後もそこだけ v13 を読み続けていた。
`chihou_monthly_rollover.py` がその関数をそのまま「一度きり評価」に使うため、
**202608 のレポートは見出しが v13 の部分月（8/1〜8/13）** という食い違った
状態で TEST 台帳に記録された。

このテストは「`chihou.calculated_indices` を読む SQL に version の直書きが
無いこと」を固定する。バインド変数（`%(ver)s` 等）は許す。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# `chihou.calculated_indices` を読み、かつ現行版で評価すべきスクリプト。
# ここに足すのではなく、SQL 側をバインド変数にすること。
_TARGETS = [
    "scripts/chihou_cutoff_venue_review.py",
    "scripts/chihou_monthly_rollover.py",
]

# `version = 13` / `version=13` / `ci.version = 14` などの数値直書き
_PINNED = re.compile(r"\bversion\s*=\s*\d+", re.IGNORECASE)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """docstring として使われている文字列ノードの id を集める。

    説明文の中で `ci.version = 13` に言及することはある（まさにこの修正の
    経緯がそう）。検査対象は**実際に実行される SQL 文字列だけ**にする。
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


@pytest.mark.parametrize("rel", _TARGETS)
def test_index_version_is_not_hardcoded_in_sql(rel: str) -> None:
    path = _ROOT / rel
    assert path.exists(), f"{rel} が見つからない（移動・改名したらこのリストも直すこと）"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        sql = node.value
        if "calculated_indices" not in sql.lower():
            continue
        for line in sql.splitlines():
            if _PINNED.search(line):
                offenders.append(f"  {rel}:{node.lineno}  {line.strip()}")

    assert not offenders, (
        "指数バージョンが SQL に直書きされている。"
        "CHIHOU_COMPOSITE_VERSION をバインドすること:\n" + "\n".join(offenders)
    )


def test_review_script_defaults_to_current_version() -> None:
    """load_db の既定が現行バージョン定数であること。"""
    from scripts.chihou_cutoff_venue_review import load_db
    from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

    captured: dict[str, object] = {}

    class _Cur:
        description = [("date",), ("course_name",), ("race_id",), ("horse_id",),
                       ("composite_index",), ("head_count",), ("finish_position",),
                       ("abnormality_code",)]

        def execute(self, _sql: str, params: dict) -> None:
            captured.update(params)

        def fetchall(self) -> list:
            return []

        def close(self) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cur:
            return _Cur()

    load_db(_Conn(), "20260801", "20260831")
    assert captured.get("ver") == CHIHOU_COMPOSITE_VERSION, (
        f"load_db の既定が v{captured.get('ver')} になっている。"
        f"現行は v{CHIHOU_COMPOSITE_VERSION}"
    )
