"""入稿・取消の締切判定（2026-08-13 新設）。

netkeirin は **発走の15分前を過ぎると商品を出せない**（取消もできない）。

## 判定の正本は kiseki 側

    backend/src/services/keirin_submission_window.py

締切の判定は **入稿バッチ（このリポジトリ）・承認API・Web の確認画面**の3箇所で
要る。`marquee.py` と同じく正本をファイルから読み込んで束縛する。
**ここで秒数や判定を定義してはいけない**（写した瞬間に「画面は押せるのに
API が拒む」を作れる）。
`tests/test_submit_window.py::test_deadline_is_not_redefined_here` が機械的に禁じている。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# keirin/src/submit_window.py → parents[2] が kiseki のリポジトリルート。
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_submission_window.py")

_MODULE_NAME = "kiseki_keirin_submission_window"


def _load_canonical() -> ModuleType:
    """kiseki 側の正本をファイルから読み込む。

    ⚠️ `sys.path` に `backend/` を足す方式は使えない（keirin にも `src`
       パッケージがあり名前が衝突する）。正本は標準ライブラリ以外を
       import しないので、ファイル指定の読み込みで安全に共有できる。

    ⚠️ 見つからないときは**黙って自前定義へ落ちない**。フォールバックは
       二重管理を静かに復活させ、ずれても誰も気づけない。
    """
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"入稿締切の正本が見つかりません: {_CANONICAL}\n"
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

SUBMIT_DEADLINE_SEC = _canonical.SUBMIT_DEADLINE_SEC
is_closed = _canonical.is_closed
seconds_until_deadline = _canonical.seconds_until_deadline
