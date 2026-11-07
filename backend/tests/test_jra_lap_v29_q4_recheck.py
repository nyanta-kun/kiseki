"""`scripts/jra_lap_v29_q4_recheck.py`（事前登録 §9）の判定・窓・合わせ方を固定する。

DB は使わない。合成データだけで検査する。
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.jra_lap_v29_q4_recheck as m
from scripts.jra_lap_v29_q3_confirm import win_delta

_SCRIPT = Path(m.__file__)


def _c(delta: float, lo: float, hi: float) -> dict:
    return {"delta_logloss": delta, "ci95": [lo, hi]}


# ---------------------------------------------------------------------------
# §9 の表
# ---------------------------------------------------------------------------


def test_action_table_all_branches() -> None:
    # Q4 単独の点推定 < 0 → 合算は見ない
    assert m.q4_action(_c(-0.001, -0.01, 0.008), _c(0.002, -0.001, 0.005)) == m.ACTION_KEEP
    # Q4 単独 ≥ 0 かつ 合算 CI 上端 < 0
    assert m.q4_action(_c(0.0, -0.01, 0.01), _c(-0.008, -0.015, -0.001)) == m.ACTION_KEEP_RECHECK
    # Q4 単独 ≥ 0 かつ 合算 CI が 0 を跨ぐ
    assert m.q4_action(_c(0.004, -0.004, 0.012), _c(-0.004, -0.010, 0.002)) == m.ACTION_ROLLBACK
    # 合算 CI が丸ごと 0 より上でも戻す（表の漏れを埋める）
    assert m.q4_action(_c(0.01, 0.002, 0.02), _c(0.005, 0.001, 0.01)) == m.ACTION_ROLLBACK
    # 上端ちょうど 0 は「< 0」ではない
    assert m.q4_action(_c(0.001, -0.01, 0.01), _c(-0.005, -0.01, 0.0)) == m.ACTION_ROLLBACK


def test_action_strings_match_prereg_table() -> None:
    doc = (_SCRIPT.parents[2] / "docs" / "jra_lap_v29_q3_confirm_plan_2026_11_07.md").read_text()
    sec9 = doc[doc.index("## 9.") :]
    for s in (m.ACTION_KEEP, m.ACTION_KEEP_RECHECK, m.ACTION_ROLLBACK):
        assert s in sec9, s


# ---------------------------------------------------------------------------
# 窓
# ---------------------------------------------------------------------------


def test_legs() -> None:
    assert (m.Q3.eval_start, m.Q3.eval_end) == ("20260701", "20260930")
    assert (m.Q4.eval_start, m.Q4.eval_end) == ("20261001", "20261231")
    assert m.REAL == (m.Q3, m.Q4)
    # 試走は VAL（2026-09-30 まで）に収まる
    assert m.hard_stop_after(m.REHEARSAL[1]) == "20261001"
    assert m.hard_stop_after(m.Q4) == "20270101"


def test_refuses_before_q4_closes() -> None:
    with pytest.raises(SystemExit):
        m.check_window_closed(m.Q4, datetime.date(2026, 11, 7), "20261001")
    with pytest.raises(SystemExit):
        m.check_window_closed(m.Q4, datetime.date(2026, 12, 31), "20261001")


def test_requires_pinned_test_start() -> None:
    # 1/1 以降は TEST_START が 20270101 に繰り上がる → 付け忘れたら止める
    with pytest.raises(SystemExit):
        m.check_window_closed(m.Q4, datetime.date(2027, 1, 2), "20270101")
    m.check_window_closed(m.Q4, datetime.date(2027, 1, 2), "20261001")


# ---------------------------------------------------------------------------
# 合わせ方（walk-forward）
# ---------------------------------------------------------------------------


def _leg_frame(race_ids: list[int], rng: np.random.Generator, sharp: float) -> pd.DataFrame:
    rows = []
    for rid in race_ids:
        n = 8
        p = rng.dirichlet(np.ones(n) * sharp)
        win = rng.choice(n, p=p)
        for h in range(n):
            rows.append(
                {
                    "race_id": rid,
                    "horse_number": h + 1,
                    "p_win": p[h],
                    "place_slots": 3,
                    "finish_position": 1 if h == win else 2 + h,
                }
            )
    return pd.DataFrame(rows)


def test_pool_concatenates_per_arm_and_matches_union() -> None:
    rng = np.random.default_rng(1)
    q3 = {"v28": _leg_frame(list(range(1, 41)), rng, 1.0), "lap": _leg_frame(list(range(1, 41)), rng, 1.2)}
    q4 = {"v28": _leg_frame(list(range(101, 131)), rng, 1.0), "lap": _leg_frame(list(range(101, 131)), rng, 1.2)}
    pooled = m.pool_legs([q3, q4])
    assert set(pooled) == {"v28", "lap"}
    assert pooled["lap"]["race_id"].nunique() == 70
    # 合算 Δ は2脚のレース数で重み付けした平均と一致する
    a, b, c = win_delta(q3, 50), win_delta(q4, 50), win_delta(pooled, 50)
    want = (a["delta_logloss"] * 40 + b["delta_logloss"] * 30) / 70
    assert c["n_races"] == 70
    assert abs(c["delta_logloss"] - want) < 1e-4


def test_pool_rejects_overlapping_windows() -> None:
    rng = np.random.default_rng(2)
    q3 = {"v28": _leg_frame([1, 2], rng, 1.0), "lap": _leg_frame([1, 2], rng, 1.0)}
    q4 = {"v28": _leg_frame([2, 3], rng, 1.0), "lap": _leg_frame([2, 3], rng, 1.0)}
    with pytest.raises(SystemExit):
        m.pool_legs([q3, q4])


# ---------------------------------------------------------------------------
# §8 の再現検査
# ---------------------------------------------------------------------------


def test_q3_reproduction_check() -> None:
    stored = {
        "n_races": 887,
        "delta_logloss": -0.01331,
        "ci95": [-0.02238, -0.00456],
        "v28_logloss": 2.08142,
        "lap_logloss": 2.06811,
    }
    same = {**stored, "delta_logloss": -0.013312, "ci95": [-0.022381, -0.004561]}
    assert m.check_q3_reproduces(same, stored) == []
    drift = {**stored, "delta_logloss": -0.0125}
    assert m.check_q3_reproduces(drift, stored)
    assert m.check_q3_reproduces({**stored, "n_races": 886}, stored)


# ---------------------------------------------------------------------------
# 別実装をしない（AST）
# ---------------------------------------------------------------------------


def test_reuses_q3_confirm_functions() -> None:
    tree = ast.parse(_SCRIPT.read_text())
    imported: set[str] = set()
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("jra_lap_v29_q3_confirm"):
            imported |= {a.name for a in node.names}
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
    assert {"fit_condition", "win_delta", "assert_no_test_rows", "ARMS", "BOOT_SEED"} <= imported
    assert not ({"fit_condition", "win_delta", "fit_predict", "race_normalize"} & defined)
    assert "fit_predict" not in _SCRIPT.read_text()
