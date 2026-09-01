"""`src/` の doctest を**通常の `pytest tests/` で必ず走らせる**。

## なぜ必要か

CI も `scripts/dev/preflight.sh` も `pytest tests/` しか叩かないので、
`src/` の docstring に書いた例は**一度も実行されていなかった**。

2026-09-01 に実際に3件が古いまま腐っていた:

| 場所 | 書いてあった値 | 実際 |
|---|---|---|
| `type_lab.sell_plans_for` | 7車の型F は `F_pay` | `F_hit`（2026-08-31 に変更） |
| `type_lab.plans_for` | 型F は `['F_hit','F_pay']` | `F_sign` が増えている |
| `combo_label.format_pred_combo` | 三連複は畳まない | 畳む（`tests/test_combo_label.py` が正） |

いずれも**商品の正本**（`src/type_lab.py`）の例で、読んだ人が古い挙動を前提に
設計を判断しうる。このリポジトリが繰り返している
「**同じ知識が複数箇所にあり、更新が同期しない**」の一形態。

## なぜ `--doctest-modules` を CI へ足すのではなくテストにしたか

CI・preflight・ローカルの3か所で起動コマンドを揃える必要があり、
**足し忘れた場所だけが素通しになる**。`tests/` に置けばどの経路からも必ず走る。
"""

from __future__ import annotations

import doctest
import importlib
import pkgutil
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

#: 🔴 ここへ足して黙らせないこと。足してよいのは「import 自体が重い／
#:    外部資源を要する」モジュールだけで、**例が古いこと**は理由にならない。
SKIP_MODULES: frozenset[str] = frozenset()


def _modules() -> list[str]:
    out = []
    for m in pkgutil.walk_packages([str(SRC)], prefix="src."):
        if m.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        if m.name in SKIP_MODULES:
            continue
        out.append(m.name)
    return sorted(out)


@pytest.mark.parametrize("name", _modules())
def test_doctests(name: str):
    try:
        mod = importlib.import_module(name)
    except Exception as e:                                    # noqa: BLE001
        pytest.skip(f"import できません（doctest の対象外）: {e}")
    res = doctest.testmod(mod, verbose=False, report=False,
                          optionflags=doctest.NORMALIZE_WHITESPACE)
    if res.failed:
        doctest.testmod(mod, verbose=False, optionflags=doctest.NORMALIZE_WHITESPACE)
        pytest.fail(
            f"{name}: doctest が {res.failed}/{res.attempted} 件失敗しました。\n"
            f"docstring の例が実装から遅れています。**実装ではなく例を直す**のが\n"
            f"既定ですが、例のほうが正しい仕様なら実装を直してください。")
