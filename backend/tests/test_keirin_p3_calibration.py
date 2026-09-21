"""3着内率の較正とレース信頼度の固定（2026-09-21 新設）。

🔴 **このファイルが無かったせいで、`confidence_pct` の挙動は一度も固定されていなかった。**
   2026-09-21 に表示値の定義を変えた（Σ=3.0 正規化を通すようにした）とき、
   既存テスト 454 件が1つも落ちなかったのがその証拠。

守るもの:
  1. 正本は **標準ライブラリしか import しない**（keirin が素の venv から
     ファイル読み込みで束縛するため）
  2. 較正も正規化も**単調変換**なのでレース内の順位を変えない
  3. **表示用**（正規化あり）と**ゲート用**（正規化なし）が別物であること
  4. 車数が違っても表示用の信頼度が比較可能であること
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.services import keirin_p3_calibration as C

CANONICAL = Path(C.__file__)
_STDLIB_TOP = {"__future__", "importlib", "math", "os", "sys", "collections", "typing"}


def test_canonical_imports_stdlib_only():
    """🔴 keirin は FastAPI も numpy も無い venv からこのファイルを直接読む。"""
    mods: list[str] = []
    for node in ast.walk(ast.parse(CANONICAL.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    bad = [m for m in mods if m.split(".")[0] not in _STDLIB_TOP]
    assert not bad, f"標準ライブラリ以外を import している: {bad}"


def test_normalizer_is_bound_from_the_canonical_file():
    """式を写していないこと（`keirin_prob_normalize` を束縛して使う）。"""
    assert C._norm.__file__.endswith("keirin_prob_normalize.py")
    assert C._norm.TOP3_SLOTS == 3


P7 = {1: 0.62, 2: 0.58, 3: 0.50, 4: 0.43, 5: 0.38, 6: 0.30, 7: 0.21}     # Σ=3.02
P9 = dict(zip(range(1, 10), [0.60, 0.55, 0.46, 0.40, 0.35, 0.30, 0.27, 0.19, 0.04]))  # Σ=3.16


def test_calibration_keeps_rank():
    """🔴 単調変換なので軸選定は狂わない。"""
    order = sorted(P7, key=lambda c: (-P7[c], c))
    cal = {c: C.calibrate_top3(p, "決勝", 5) for c, p in P7.items()}
    assert sorted(cal, key=lambda c: (-cal[c], c)) == order


def test_display_and_gate_are_different_quantities():
    """表示用は Σ=3.0 正規化を通し、ゲート用は通さない。"""
    gate = C.calibrated_p3_sum_top2(P9, None, None)
    disp = C.normalized_calibrated_p3_sum_top2(P9, None, None)
    assert gate is not None and disp is not None
    # 9車は Σp3 が 3.0 より大きいので、正規化すると小さくなる
    assert disp < gate


def test_display_sum_is_nearly_scale_free():
    """モデル出力が一律 k 倍されても表示用の値はほぼ動かない（ゲート用は動く）。

    ⚠️ **完全な不変ではない。** 順序が「較正 → 正規化」で、Platt 較正は
       ロジット空間のアフィン変換なので入力の定数倍に対して等価ではないため。
       実測の残差は 6% の倍率に対して 0.03%（1.19100 → 1.19068）で、
       表示は整数%へ丸めるので**見た目は動かない**。
       順序を「正規化 → 較正」にすれば厳密に不変になるが、較正係数は
       **生の p3 の上で当てた**ものなので、当てた分布へ入力を揃える方を採る。
    """
    scaled = {c: v * 1.06 for c, v in P7.items()}
    a = C.normalized_calibrated_p3_sum_top2(P7, None, None)
    b = C.normalized_calibrated_p3_sum_top2(scaled, None, None)
    assert a == pytest.approx(b, rel=1e-3)
    assert C.confidence_pct(P7) == C.confidence_pct(scaled)      # 表示は一致する
    # ゲート用（正規化なし）は倍率でそのまま動く
    assert C.calibrated_p3_sum_top2(P7, None, None) != pytest.approx(
        C.calibrated_p3_sum_top2(scaled, None, None), abs=1e-3)


def test_confidence_uses_the_normalized_quantity():
    expect = int(100.0 * C.normalized_calibrated_p3_sum_top2(P9, None, None)
                 / C.CONFIDENCE_FULL_SUM + 0.5)
    assert C.confidence_pct(P9) == expect


def test_confidence_bounds_and_none_cases():
    assert C.confidence_pct({1: 1.0, 2: 1.0, 3: 0.0}) == 100
    assert C.confidence_pct({1: 0.5}) is None
    assert C.confidence_pct({}) is None
    assert 0 <= C.confidence_pct(P7) <= 100


def test_confidence_axes_unchanged_by_normalization():
    """答え合わせに使う2車は正規化の前後で同じ（順位が変わらないので当然）。"""
    assert C.confidence_axes(P9) == tuple(sorted(P9, key=lambda c: (-P9[c], c))[:2])
