"""特徴量キャッシュの鍵は**コードの版**を含む（2026-09-21 新設）。

## 何を守るか

旧実装の鍵は `期間 × len(FEATURE_COLS_WT) × (行数, MAX(race_date))` だけで、
**列数が変わらない修正では鍵が変わらなかった**。過去期間は行数も MAX(date) も
動かないので、修正前のキャッシュが**永久に再利用される**。

実害（2026-09-21 実測）: 2026-09-20 に `MED_RACE_POINT_FILL` を固定値化したのに、
9/11 に作られた `wtfeat_20250601_20260630_f70_210526_20260630.pkl` は旧の
範囲依存中央値のまま（`race_point` に 85.40 が826行）。同日の A/B 2本が
このキャッシュを読んでいた。

⚠️ **コメント・docstring の編集では無効化しない。** このリポジトリは注記が厚く、
   素のソースをハッシュすると注記を足すたびに 227秒の再計算が走る。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT, _feature_code_version,
)

SRC = REPO / "src" / "preprocessing" / "feature_wt.py"


def _version_of(text: str) -> str:
    """`_feature_code_version` と同じ規則を、任意のソース文字列に当てる。"""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return hashlib.sha1(ast.dump(tree).encode("utf-8")).hexdigest()[:10]


def test_version_matches_the_helper():
    assert _version_of(SRC.read_text(encoding="utf-8")) == _feature_code_version()


def test_comments_do_not_invalidate_the_cache():
    """注記を足しただけで 227秒の再計算を走らせない。"""
    base = SRC.read_text(encoding="utf-8")
    withcomment = base.replace("import ast\n", "import ast\n# 注記を足しただけ\n", 1)
    assert withcomment != base
    assert _version_of(withcomment) == _version_of(base)


def test_docstrings_do_not_invalidate_the_cache():
    body = "def f():\n    \"\"\"説明\"\"\"\n    return 1\n"
    other = "def f():\n    \"\"\"別の説明をたくさん書いた\"\"\"\n    return 1\n"
    assert _version_of(body) == _version_of(other)


def test_changing_a_fill_constant_invalidates_the_cache():
    """🔴 これが効かなかったのが 2026-09-20 の実害の正体。"""
    base = SRC.read_text(encoding="utf-8")
    changed = base.replace("MED_RACE_POINT_FILL = 85.55",
                           "MED_RACE_POINT_FILL = 85.56", 1)
    assert changed != base, "定数の綴りが変わったらこのテストを直すこと"
    assert _version_of(changed) != _version_of(base)


def test_changing_the_feature_list_invalidates_the_cache():
    base = SRC.read_text(encoding="utf-8")
    changed = base.replace('    "race_point",\n', '    "race_point",\n    "gear_ratio",\n', 1)
    assert changed != base
    assert _version_of(changed) != _version_of(base)


def test_cache_path_contains_the_version():
    src = SRC.read_text(encoding="utf-8")
    assert 'f"wtfeat_{tag}_f{len(FEATURE_COLS_WT)}_v{ver}_{fp}.pkl"' in src
    assert "ver = _feature_code_version()" in src
    # 掃除の glob が版違いも拾うこと（古い鍵が残り続けない）
    assert 'glob(f"wtfeat_{tag}_f{len(FEATURE_COLS_WT)}_*.pkl")' in src


def test_feature_list_is_not_empty():
    assert len(FEATURE_COLS_WT) > 50
