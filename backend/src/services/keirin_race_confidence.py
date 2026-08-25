"""レース信頼度（0〜100%）の判定を keirin 側の正本から束縛する（2026-08-25 新設）。

## なぜファイル読み込みなのか

正本は **keirin 側の `keirin/src/p3_calibration.py`**（較正係数と同じ場所に置く）。
`import` はできない——keirin は自分の venv（FastAPI も SQLAlchemy も無い）で動き、
かつ `src` というパッケージ名が kiseki 側と衝突するため。
`keirin/src/marquee.py` が kiseki の正本を読み込むのと**同じ手口の逆方向**。

🔴 **ここへ計算式や較正係数を写してはいけない。** 写した瞬間に、ゲートが見る値と
   画面に出る値がずれる。「同じレースの3着内確率が3つあって一致しない」という
   既知の痛みを増やすことになる。

🔴 **見つからないときは黙って自前計算へ落ちない。** フォールバックは二重管理を
   静かに復活させ、ずれても誰も気づけない。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_CANONICAL = (Path(__file__).resolve().parents[3]
              / "keirin" / "src" / "p3_calibration.py")

_MODULE_NAME = "keirin_p3_calibration_canonical"


def _load_canonical() -> ModuleType:
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"レース信頼度の正本が見つかりません: {_CANONICAL}\n"
            "keirin は kiseki リポジトリ内（<kiseki>/keirin）にある前提です。"
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CANONICAL)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise ImportError(f"正本を読み込めません: {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_canonical = _load_canonical()

CONFIDENCE_FULL_SUM = _canonical.CONFIDENCE_FULL_SUM
confidence_pct = _canonical.confidence_pct


def confidence_from_entries(entries, race_type=None, cup_grade=None) -> int | None:
    """出走表の行（`pred_top3_pct` を持つ dict）からレース信頼度を出す。

    ⚠️ `pred_top3_pct` は **%スケール**で入っているので 0-1 へ直して渡す。
       ここを間違えると常に 100% になる（正本は 0-1 前提）。
    """
    probs = {}
    for e in entries or []:
        v = e.get("pred_top3_pct")
        fno = e.get("frame_no")
        if v is None or fno is None:
            continue
        probs[int(fno)] = float(v) / 100.0
    if len(probs) < 2:
        return None
    return confidence_pct(probs, race_type, cup_grade)
