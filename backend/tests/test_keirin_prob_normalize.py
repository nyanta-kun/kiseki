"""確率のレース内正規化（`keirin_prob_normalize`）の固定（2026-09-21）。

🔴 このモジュールは **keirin 側が自分の venv からファイル読み込みで束縛する**
   （`keirin/src/prob_normalize.py`）。したがって:
     - 標準ライブラリ以外を import してはいけない（足すと入稿だけが静かに落ちる）
     - keirin 側の re-export が正本と同じ関数を指していること
   をテストで固定する。
"""
from __future__ import annotations

import ast
import doctest
from pathlib import Path

import pytest

from src.services import keirin_prob_normalize as N

CANONICAL = Path(N.__file__)
KEIRIN_SIDE = CANONICAL.resolve().parents[3] / "keirin" / "src" / "prob_normalize.py"

_STDLIB_OK = {"__future__", "collections", "collections.abc", "math", "typing"}


def test_doctests_pass():
    res = doctest.testmod(N, verbose=False)
    assert res.failed == 0, f"doctest 失敗 {res.failed}/{res.attempted}"


def test_canonical_imports_stdlib_only():
    """🔴 keirin は FastAPI も numpy も無い venv からこのファイルを直接読む。"""
    tree = ast.parse(CANONICAL.read_text())
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    bad = [m for m in mods if m.split(".")[0] not in {x.split(".")[0] for x in _STDLIB_OK}]
    assert not bad, f"標準ライブラリ以外を import している: {bad}"


@pytest.mark.skipif(not KEIRIN_SIDE.exists(), reason="keirin/ が無い環境")
def test_keirin_side_reexports_only():
    """keirin 側に式や定数が**写されていない**こと（二重管理の防止）。"""
    src = KEIRIN_SIDE.read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            pytest.fail(f"keirin 側で関数を定義している: {node.name}（正本から re-export すること）")
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper() and not t.id.startswith("_"):
                    assert isinstance(node.value, ast.Attribute), (
                        f"keirin 側で定数 {t.id} を直接定義している（正本から取ること）")


@pytest.mark.parametrize("slots", [1, 3])
def test_sum_matches_slots(slots):
    probs = {1: 0.9, 2: 0.7, 3: 0.55, 4: 0.4, 5: 0.3, 6: 0.2, 7: 0.1}
    out = N.normalize_to_slots(probs, slots)
    assert sum(out.values()) == pytest.approx(slots, abs=1e-9)
    assert set(out) == set(probs)


def test_rank_is_unchanged():
    """🔴 単調変換なのでレース内の順位は絶対に変わらない（軸選定は狂わない）。"""
    probs = {1: 0.31, 2: 0.62, 3: 0.11, 4: 0.48, 5: 0.29, 6: 0.55, 7: 0.07}
    order = sorted(probs, key=lambda c: (-probs[c], c))
    for fn in (N.normalize_top3, N.normalize_win):
        out = fn(probs)
        assert sorted(out, key=lambda c: (-out[c], c)) == order


def test_car_count_bias_is_removed():
    """車数が違っても正規化後の合計は揃う（＝レース間で比べられる）。"""
    seven = {c: 0.433 for c in range(1, 8)}      # Σ=3.031（7車の実測に近い）
    nine = {c: 0.351 for c in range(1, 10)}      # Σ=3.159（9車の実測に近い）
    assert sum(seven.values()) != pytest.approx(sum(nine.values()), abs=0.01)
    assert sum(N.normalize_top3(seven).values()) == pytest.approx(
        sum(N.normalize_top3(nine).values()), abs=1e-9)


def test_zero_and_empty_are_left_alone():
    """🔴 合計0を均等割りにしない（「情報が無い」を偽の情報に変えないため）。"""
    assert N.normalize_top3({}) == {}
    assert N.normalize_top3({1: 0.0, 2: 0.0}) == {1: 0.0, 2: 0.0}


def test_clip_is_not_renormalized():
    """クリップ後に再正規化しない（JRA v28 `normalize_place_to_slots` と同じ判断）。"""
    probs = {1: 0.99, 2: 0.01, 3: 0.01}          # 1番が 1.0 を超えるよう強く引き伸ばす
    out = N.normalize_to_slots(probs, 3)
    assert out[1] == pytest.approx(1.0 - N.PROB_EPS)
    assert sum(out.values()) < 3.0               # 崩れをその1車に閉じ込める


def test_top2_slot_sum_is_scale_free():
    """同じ形なら Σ が違っても同じ値になる（生の axis_sum はここがずれる）。"""
    a = {1: 0.60, 2: 0.60, 3: 0.60, 4: 0.40, 5: 0.30, 6: 0.30, 7: 0.20}   # Σ=3.00
    b = {c: v * 1.05 for c, v in a.items()}                               # Σ=3.15
    assert N.top2_slot_sum(a) == pytest.approx(N.top2_slot_sum(b), abs=1e-9)
    raw_a = sum(sorted(a.values(), reverse=True)[:2])
    raw_b = sum(sorted(b.values(), reverse=True)[:2])
    assert raw_a != pytest.approx(raw_b, abs=1e-3)      # 生だとずれる、が対比
    assert N.top2_slot_sum(None if False else {1: 0.5}) is None
