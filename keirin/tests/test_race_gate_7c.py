"""レース選別スコア（`src/race_gate_7c.py`）の固定検査。

🔴 **このスコアは本番の判定に入っていない**（2026-08-18 の walk-forward A/B で
   不採用。件数を揃えると差が消え、改善の正体は「少なく賭けた」だった——
   `docs/analysis/56-race-selection-meta.md`）。7C の選別は
   `RANK_7C_P3_SUM_MIN = 1.44` のまま。ここで固定しているのは
   **再検証したくなったときに同じものを測れる状態**である。

固定しているのは「数値がいくつか」ではなく **壊れ方**:

- 4特徴の**符号**（`gap23`/`same_line` が正・`p_ent` が負）。反転すると
  「混戦ほど買う」スコアになり、しかも件数は同じなので気づけない
- **判定不能で 0.0 を返さない**（None を返して呼び出し側に判断させる）
"""
from __future__ import annotations

import pytest

from src import race_gate_7c as G
from src.strategy_wt import RANK_7C_P3_SUM_MIN

RT, CG = "一般", 2          # F級・その他（較正は恒等に近いセル）


def _probs(vals: list[float]) -> dict[int, float]:
    return {i + 1: v for i, v in enumerate(vals)}


BASE = [0.80, 0.62, 0.55, 0.45, 0.35, 0.15, 0.08]


def test_score_none_when_undecidable():
    """車数が足りない/値が無いときは None（＝判定不能）。0.0 を返さないこと。"""
    assert G.score({}, None, RT, CG) is None
    assert G.score({1: 0.5, 2: 0.4}, None, RT, CG) is None
    assert G.passes(None) is False


def test_gap23_sign_is_positive():
    """2位と3位の差が開くほどスコアは上がる（軸2の指定が確かになる）。"""
    tight = G.score(_probs([0.80, 0.62, 0.615, 0.45, 0.35, 0.15, 0.08]), None, RT, CG)
    wide = G.score(_probs([0.80, 0.62, 0.30, 0.28, 0.25, 0.15, 0.08]), None, RT, CG)
    assert wide > tight


def test_same_line_sign_is_positive():
    """軸2車が同ラインならスコアは上がる。"""
    p = _probs(BASE)
    same = G.score(p, {1: "a", 2: "a", 3: "b", 4: "b", 5: "c", 6: "c", 7: "d"}, RT, CG)
    diff = G.score(p, {1: "a", 2: "b", 3: "b", 4: "b", 5: "c", 6: "c", 7: "d"}, RT, CG)
    assert same > diff
    # ライン情報が取れない日は same_line=0 扱い（例外にせず、黙って全滅もさせない）
    assert G.score(p, None, RT, CG) == pytest.approx(diff)


def test_entropy_sign_is_negative():
    """混戦（エントロピーが高い）ほどスコアは下がる。"""
    flat = G.score(_probs([0.44] * 7), None, RT, CG)
    peaked = G.score(_probs([0.95, 0.80, 0.30, 0.20, 0.15, 0.10, 0.05]), None, RT, CG)
    assert peaked > flat


def test_calibration_is_applied():
    """決勝（過大評価を潰すセル）は同じ生確率でもスコアが下がる。"""
    p = _probs(BASE)
    normal = G.score(p, None, "一般", 2)
    final = G.score(p, None, "決勝", 2)
    assert final < normal


def test_production_gate_is_unchanged():
    """🔴 本番の 7C 選別は据え置き（このスコアは判定に入っていない）。

    ここが変わるときは `docs/analysis/56-race-selection-meta.md` の
    「件数を揃えると差が消える」を覆す測定が先にあるはず。
    """
    import inspect

    from src import strategy_wt

    assert RANK_7C_P3_SUM_MIN == 1.44
    src = inspect.getsource(strategy_wt.rank_7c_daily_select)
    assert "_gate_p3_sum(c) >= RANK_7C_P3_SUM_MIN" in src
    assert "gate7c_score" not in src
