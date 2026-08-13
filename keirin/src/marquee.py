"""看板レース（売上が集まりやすいレース）の検出（2026-08-09 新設）。

## なぜ要るのか

2026-08-08 のレース単位分析で、**当日売上 5,060pt の 84% が「外れたレース」に
集中**していた。売れたのは全て看板レース（決勝・特選クラス）で、逆に高配当を
返した準決勝・予選は買い手0。「売れた時に当たらず、当たり出した時に売れていない」。

ユーザー決定（2026-08-09）: **看板レースとその前後には必ず推奨を出す**。
目的関数は売上加重の的中率（ROI悪化は許容）。

## 判定の正本は kiseki 側（2026-08-11 一本化）

    backend/src/services/keirin_marquee.py

看板判定は **自動入稿（このリポジトリ）と Web一覧の★表示**の2箇所で要る。
keirin が別リポジトリだった間はキーワードを**両方に写して**いたが、
kiseki へ統合されたのでファイルから直接読み込む形にした。
**ここでキーワードを定義してはいけない**（写した瞬間に
「★は付くのに入稿されない」またはその逆を作れる）。
`tests/test_marquee.py::test_keywords_are_not_redefined_here` が機械的に禁じている。

判定そのものの注意点（準決勝の部分一致・レース番号で判定しない等）は
正本の docstring を見ること。

## このモジュール固有の責務

    前後 : 看板レースの前後1レース（同一開催）— `marquee_race_nos()`

Web は★を付けるだけなので「前後」は要らず、入稿側だけが使う。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# keirin/src/marquee.py → parents[2] が kiseki のリポジトリルート。
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_marquee.py")

_MODULE_NAME = "kiseki_keirin_marquee"


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
            f"看板レース判定の正本が見つかりません: {_CANONICAL}\n"
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

MARQUEE_KEYWORDS = _canonical.MARQUEE_KEYWORDS
MARQUEE_EXCLUDE = _canonical.MARQUEE_EXCLUDE
BIG_EVENT_KEYWORDS = _canonical.BIG_EVENT_KEYWORDS

# 正本の関数をそのまま束縛する（ラップし直すと分岐が生まれるため）。
# kiseki 側の名前は `is_marquee_race`、keirin 側の呼び出し名は `is_marquee_type`。
is_marquee_type = _canonical.is_marquee_race
is_big_event_type = _canonical.is_big_event_race
# 🔴 穴埋めの対象判定はこれを使う（看板 or 大会の予選）。
#    `is_marquee_type` を直接見ると大会の予選が漏れる。
is_fill_target = _canonical.is_fill_target


def marquee_race_nos(races: list[dict]) -> set[int]:
    """同一開催のレース一覧から、**穴埋め対象**とその前後のレース番号を返す。

    races: [{"race_no": int, "race_type": str|None}, …]（同一開催ぶん）

    🔴 対象は**看板 または 大会（6日制の特別開催）の予選**（`is_fill_target`）。
       2026-08-13 まで看板だけを見ており、GI級の開催（オールスター競輪・松山）で
       **6R〜11R が丸ごと無推奨**になっていた。売上は他会場の5.0倍の場所だった。

    ⚠️ 「前後」は**レース番号の±1**。実際に隣接するレースが存在するかは
       呼び出し側が `races` に含まれるかで判断する（欠番があっても
       存在しない番号を返さない）。
    """
    present = {int(r["race_no"]) for r in races if r.get("race_no") is not None}
    marquee = {int(r["race_no"]) for r in races
               if r.get("race_no") is not None and is_fill_target(r.get("race_type"))}
    out = set(marquee)
    for n in marquee:
        out |= {n - 1, n + 1}
    return out & present
