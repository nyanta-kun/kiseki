"""`scripts/jra_lap_v29_q3_confirm.py` の判定と窓の検査。

事前登録 `docs/jra_lap_v29_q3_confirm_plan_2026_11_07.md` の §3（2026Q4 を読まない）と
§4（CI だけで3通りに判定する）を固定する。
"""

from __future__ import annotations

import pandas as pd
import pytest

import scripts.jra_lap_v29_q3_confirm as m


def test_verdict_three_way() -> None:
    assert m.verdict({"delta_logloss": -0.01, "ci95": [-0.02, -0.001]}) == "確認成功"
    assert m.verdict({"delta_logloss": -0.01, "ci95": [-0.02, 0.0]}) == "判断保留"
    assert m.verdict({"delta_logloss": -0.001, "ci95": [-0.01, 0.008]}) == "判断保留"
    assert m.verdict({"delta_logloss": 0.0, "ci95": [-0.01, 0.01]}) == "確認失敗"
    assert m.verdict({"delta_logloss": 0.004, "ci95": [-0.005, 0.013]}) == "確認失敗"


def test_window_constants() -> None:
    assert (m.EVAL_START, m.EVAL_END, m.HARD_STOP) == ("20260701", "20260930", "20261001")


def test_rejects_q4_rows() -> None:
    ok = pd.DataFrame({"date": ["20260630", "20260930"]})
    m.assert_no_test_rows(ok)
    bad = pd.DataFrame({"date": ["20260930", "20261001"]})
    with pytest.raises(SystemExit):
        m.assert_no_test_rows(bad)


def test_arms_are_v28_plus_lap_only() -> None:
    assert set(m.ARMS) == {"v28", "lap"}
    v28, lap = m.ARMS["v28"], m.ARMS["lap"]
    assert len(v28) == 38
    assert lap[: len(v28)] == v28
    assert len(lap) == 45
    assert set(m.GD0_COLS) <= set(v28)


def test_train_valid_split_ends_before_eval() -> None:
    dates = pd.date_range("2025-01-01", "2026-06-30", freq="D").strftime("%Y%m%d")
    train = pd.DataFrame({"date": list(dates) * 30})
    tr, va, cut = m.split_train_valid(train, 90)
    assert va["date"].max() < m.EVAL_START
    assert tr["date"].max() <= cut < va["date"].min()
