"""確率のレース内正規化。**正本は kiseki 側**（2026-09-21 新設）。

正本: `backend/src/services/keirin_prob_normalize.py`
（`marquee.py` / `cup_grade.py` / `p3_calibration.py` と同じ手口・同じ向き）。

🔴 **ここへ式や定数を写してはいけない。** 写した瞬間に、ゲートが見る値と
   画面に出る値がずれる。
🔴 **見つからないときは黙って自前計算へ落ちない**（フォールバックは二重管理を
   静かに復活させる）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_prob_normalize.py")

_MODULE_NAME = "kiseki_keirin_prob_normalize"


def _load_canonical() -> ModuleType:
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"確率正規化の正本が見つかりません: {_CANONICAL}\n"
            "keirin は kiseki リポジトリ内（<kiseki>/keirin）で動かす前提です。"
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CANONICAL)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise ImportError(f"正本を読み込めません: {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_canonical = _load_canonical()

TOP3_SLOTS = _canonical.TOP3_SLOTS
WIN_SLOTS = _canonical.WIN_SLOTS
PROB_EPS = _canonical.PROB_EPS
normalize_to_slots = _canonical.normalize_to_slots
normalize_top3 = _canonical.normalize_top3
normalize_win = _canonical.normalize_win
slot_share = _canonical.slot_share
top2_slot_sum = _canonical.top2_slot_sum
