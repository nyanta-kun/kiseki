"""3着内率の後段較正とレース信頼度。**正本は kiseki 側**（2026-08-25 移設）。

正本: `backend/src/services/keirin_p3_calibration.py`
（`marquee.py` / `cup_grade.py` と同じ手口・同じ向き）。

## なぜ kiseki が正本なのか

kiseki の backend イメージは `./backend` だけをビルドコンテキストにするので
**コンテナの中に `keirin/` は無い**。keirin 側を正本にすると Web から参照できず、
2026-08-25 のデプロイで backend コンテナが起動に失敗した。

🔴 **ここへ係数や計算式を写してはいけない。** 写した瞬間に、ゲートが見る値と
   画面に出る値がずれる。「同じレースの3着内確率が3つあって一致しない」という
   既知の痛みを増やすことになる。
🔴 **見つからないときは黙って自前計算へ落ちない**（フォールバックは二重管理を
   静かに復活させる）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_p3_calibration.py")

_MODULE_NAME = "kiseki_keirin_p3_calibration"


def _load_canonical() -> ModuleType:
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"3着内率較正の正本が見つかりません: {_CANONICAL}\n"
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

race_type_group = _canonical.race_type_group
grade_group = _canonical.grade_group
coefficients = _canonical.coefficients
calibrate_top3 = _canonical.calibrate_top3
calibrated_p3_sum_top2 = _canonical.calibrated_p3_sum_top2
CONFIDENCE_FULL_SUM = _canonical.CONFIDENCE_FULL_SUM
confidence_pct = _canonical.confidence_pct
