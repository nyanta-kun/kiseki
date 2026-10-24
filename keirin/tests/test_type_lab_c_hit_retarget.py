"""C_hit を的中重視（2.5万・段の広めと同じ詰め方）へ替え、A_hit の販売を止めた件（2026-10-24）。

ユーザー決定: 「A は推奨をやめる」「C は高額ではなく的中率を目指す・ガミにならない線で」。
根拠と事前登録: `docs/type_lab/c_hit_retarget_2026_10_24.md`。
"""
from __future__ import annotations

import itertools

import pytest

import src.type_lab as T
from src.type_lab import (
    C_HIT_TARGET,
    HIT_BAND_TARGET,
    HIT_POINT_GATE_MIN,
    HIT_POINT_GATE_PLANS,
    NINE_CAR_PLAN_OVERRIDES,
    PLANS,
    RaceShape,
    build_with_gate_fallback,
    rule_version,
)


def _row(stake, odds, n_entries=7, plan_key="C_hit", race_type="予選", axis_sum=1.50):
    legs = [{"combo": "1-2-3", "stake": stake, "pred_odds": odds}]
    return dict(plan_key=plan_key, race_type=race_type, axis_sum=axis_sum,
                n_entries=n_entries, pred_mean_payout=stake * odds,
                pred_min_payout=stake * odds, legs=legs)


def test_定数():
    assert C_HIT_TARGET == 25_000
    assert HIT_POINT_GATE_PLANS == frozenset({"C_hit"})
    assert HIT_POINT_GATE_MIN == 10_000, "ガミにならない線（予算1万円）"
    assert "C_hit" not in T.GATE_FALLBACK


def test_7車のC_hitは悪い側1万円で判定する(monkeypatch):
    import scripts.netkeirin_submit_type_lab as m
    monkeypatch.setattr(m, "tier_realized_min_payout", lambda *a, **k: 10_000.0)
    assert m._gate_reason(_row(5000, 4.0)) is None
    monkeypatch.setattr(m, "tier_realized_min_payout", lambda *a, **k: 9_999.0)
    got = m._gate_reason(_row(5000, 4.0))
    assert got is not None and got[0] == m.SKIP_GATE_MEAN_PAYOUT
    assert "10,000円" in got[1]


def test_7車のC_hitは平均2万ゲートを使わない():
    """平均は 1.9万（旧ゲートなら落ちる）でも、悪い側が1万円を超えれば通す。"""
    import scripts.netkeirin_submit_type_lab as m
    row = _row(5000, 3.8)
    assert row["pred_mean_payout"] < 20_000
    assert m._gate_reason(row) is None


def test_7車のC_hitも2倍未満の目は落とす():
    import scripts.netkeirin_submit_type_lab as m
    got = m._gate_reason(_row(9000, 1.9))
    assert got is not None and got[0] == m.SKIP_GATE_POINT_ODDS


def test_9車のC_hitは従来の平均2万ゲート():
    import scripts.netkeirin_submit_type_lab as m
    got = m._gate_reason(_row(5000, 3.8, n_entries=9))
    assert got is not None and "平均想定払戻" in got[1]


def _board():
    perms = list(itertools.permutations(range(1, 8), 3))
    odds = {k: 6.0 + 2.0 * i for i, k in enumerate(perms)}
    prob = {k: 1.0 / (i + 2) for i, k in enumerate(perms)}
    s = sum(prob.values())
    return odds, {k: v / s for k, v in prob.items()}


def test_9車は旧定義の5万ダッチで組む():
    odds, prob = _board()
    shape = RaceShape("C", 1.5, 0, 0.05, False, (1, 2, 3, 4, 5, 6, 7), 1.5)
    got9 = build_with_gate_fallback(shape, PLANS["C_hit"], odds, prob, 9)
    got7 = build_with_gate_fallback(shape, PLANS["C_hit"], odds, prob, 7)
    assert got9 and got7
    assert got9[2].structure == "signboard" and got9[2].target == HIT_BAND_TARGET
    assert got7[2].structure == "tier_firm" and got7[2].target == C_HIT_TARGET
    assert got9[2] is NINE_CAR_PLAN_OVERRIDES["C_hit"]


@pytest.mark.parametrize("n", [7, 9])
def test_版は新しいゲートと9車の旧定義で割れる(monkeypatch, n):
    v0 = rule_version(n)
    if n == 7:
        monkeypatch.setattr(T, "HIT_POINT_GATE_MIN", 12_000)
    else:
        monkeypatch.setitem(T.NINE_CAR_PLAN_OVERRIDES, "C_hit",
                            T.replace(T.NINE_CAR_PLAN_OVERRIDES["C_hit"], target=40_000))
    assert rule_version(n) != v0
