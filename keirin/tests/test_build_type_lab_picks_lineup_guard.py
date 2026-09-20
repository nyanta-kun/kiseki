"""生成側にも並び・印の欠測ガードを掛ける（2026-09-20 監査 item6）。

`missing_market_inputs` は 2026-08-26 に**入稿側**（`netkeirin_submit_type_lab.py`）
へ入ったが、**生成側**（`build_type_lab_picks.py`）は退化した入力のまま
p3・型・軸を計算して `type_lab_picks` に書き続けていた。

欠測は欠測として扱われない——印なし＝最弱・ライン無し＝全員同ラインと読まれ、
エラーも警告も出ないまま型判定だけが静かにずれる（`keirin/docs/…/notes.md` item6）。
実売への実害は 8/29 以降 0件だが、それは**入稿側の再判定ループに依存した結果論**で、
生成側が止めていたわけではない。`type_lab_picks` を読む新しい消費者（画面・分析）が
増えた時点で無防備に露出する。

🔴 **ここで固定したいのは「列名の詰め替え」**。`_load_entries` は `mark` /
   `line_group` を返し、正本（`src/entry_health.py`）は `prediction_mark` /
   `line_group` を読む。詰め替えを間違えても例外は出ず、
   **全レースが「印が全車ゼロ」扱いで見送られる**（商品が静かに 0 件になる）。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "build_type_lab_picks", REPO / "scripts" / "build_type_lab_picks.py")
BTL = importlib.util.module_from_spec(_spec)
sys.modules["build_type_lab_picks"] = BTL
_spec.loader.exec_module(BTL)


def _ent(marks: list[int], lines: list[int]) -> dict:
    """`_load_entries` が返す形（車番 → 行）。"""
    return {i + 1: {"mark": m, "line_group": g, "frame_no": i + 1}
            for i, (m, g) in enumerate(zip(marks, lines))}


def test_印も並びも揃っていれば通す():
    assert BTL._lineup_issue(_ent([1, 2, 3, 0, 0, 0, 0], [1, 1, 2, 2, 3, 3, 3])) is None


def test_印が全車ゼロなら止める():
    got = BTL._lineup_issue(_ent([0] * 7, [1, 1, 2, 2, 3, 3, 3]))
    assert got == "WT印が全車ゼロ"


def test_並びが未取得なら止める():
    """全員が同じライン＝並びが公開される前の姿（2026-08-26 熊本で実際に入稿された）。"""
    got = BTL._lineup_issue(_ent([1, 2, 3, 0, 0, 0, 0], [1] * 7))
    assert got is not None and "並び" in got


def test_判定は正本へ委譲している():
    """🔴 ここに条件を書き直すと入稿側と食い違う（片方だけ通る状態になる）。"""
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "from src.entry_health import missing_market_inputs" in src
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_lineup_issue")
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "missing_market_inputs" in calls


def test_生成の本体がガードを呼んでいる():
    """🔴 ヘルパーだけ足して呼び忘れると、検査は緑のまま本番は素通りになる。"""
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_live")
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_lineup_issue" in calls
