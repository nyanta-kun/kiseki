"""開催グレード（GP/GI/GII/GIII/FI/FII）の判定（2026-08-14 新設）。

判定の正本は kiseki 側の `backend/src/services/keirin_cup_grade.py`。
`marquee.py` と同じくファイルから読み込んで束縛する。
**ここで対応表や閾値を定義してはいけない**（写した瞬間にずれる）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# keirin/src/marquee.py → parents[2] が kiseki のリポジトリルート。
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_cup_grade.py")

_MODULE_NAME = "kiseki_keirin_cup_grade"


def _load_canonical() -> ModuleType:
    """kiseki 側の正本をファイルから読み込む。

    ⚠️ `sys.path` に `backend/` を足す方式は使えない。keirin にも `src`
       パッケージがあり **名前が衝突する**ため。正本は標準ライブラリ以外を
       import しないので、ファイル指定の読み込みで安全に共有できる。

    ⚠️ 見つからないときは**黙って自前定義へ落ちない**。フォールバックは
       二重管理を静かに復活させ、ずれても誰も気づけない。
    """
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"開催グレード判定の正本が見つかりません: {_CANONICAL}\n"
            "keirin は kiseki リポジトリ内（<kiseki>/keirin）で動かす前提です。"
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CANONICAL)
    if spec is None or spec.loader is None:            # pragma: no cover - 実質起きない
        raise ImportError(f"正本を読み込めません: {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_canonical = _load_canonical()

GRADE_LABELS = _canonical.GRADE_LABELS
BIG_EVENT_MIN_GRADE = _canonical.BIG_EVENT_MIN_GRADE
grade_label = _canonical.grade_label
is_big_event_grade = _canonical.is_big_event_grade
is_known_grade = _canonical.is_known_grade
