"""7車の B/C/D を三連複の二軸流しへ替えた件（2026-11-21 ユーザー決定「b,c,d置き換えて。3も許容」）。

根拠: `docs/type_lab/stable_2car_2026_11_21.md` §7・`type_lab.TRIO_HIT_PLANS` の節。
ここで固定するのは:
  1. 7車は 二軸＋相手（3着内率順・最人気を外さない）の三連複・ダッチ（B/C 3点・D 4点）
  2. 9車は 11-20 までの定義のまま
  3. 7車の三連複行には入稿の払戻ゲートを当てない（9車と三連単の行には当てる）
  4. 文面が券種と一致する
"""
from __future__ import annotations

import itertools

import pytest

import src.type_lab as T
from src.type_lab import (
    BUDGET, HIT_BAND_TARGET, MIN_PAYOUT_MULT, NINE_CAR_PLAN_OVERRIDES, PLANS,
    RaceShape, TRIO_HIT_N_ENTRIES, TRIO_HIT_PLANS, build_with_gate_fallback,
    rule_version,
)


def _shape(t: str) -> RaceShape:
    return RaceShape(t, 1.50, 0, 0.05, True, (3, 1, 5, 2, 7, 4, 6), 1.2, ())


def _trio_board(odds_of=lambda k: 6.0):
    ks = [frozenset(c) for c in itertools.combinations(range(1, 8), 3)]
    odds = {k: odds_of(k) for k in ks}
    return odds, {k: 1.0 / len(ks) for k in ks}


def test_7車の定義():
    assert TRIO_HIT_PLANS == frozenset({"B_hit", "C_hit", "D_hit"})
    assert TRIO_HIT_N_ENTRIES == 7
    for key, n in (("B_hit", 3), ("C_hit", 3), ("D_hit", 4)):
        p = PLANS[key]
        assert (p.bet_type, p.structure, p.n_partners, p.alloc) == ("trio", "axis2_flow", n, "dutch"), key
        assert p.target == 0 and p.max_legs == 0


def test_9車は替えていない():
    b, c, d = (NINE_CAR_PLAN_OVERRIDES[k] for k in ("B_hit", "C_hit", "D_hit"))
    assert (b.bet_type, b.structure, b.alloc, b.target) == ("trifecta", "signboard", "dutch", HIT_BAND_TARGET)
    assert b.floor_mult == MIN_PAYOUT_MULT
    assert (c.bet_type, c.structure, c.target) == ("trifecta", "signboard", HIT_BAND_TARGET)
    assert (d.bet_type, d.structure, d.n_partners, d.alloc) == ("trio", "axis2_drop_fav", 3, "conf")
    assert d.floor_mult == MIN_PAYOUT_MULT


@pytest.mark.parametrize("key,n", [("B_hit", 3), ("C_hit", 3), ("D_hit", 4)])
def test_7車は二軸と3着内率順の相手で組む(key, n):
    s = _shape(key[0])
    # 最人気（オッズ最小）の相手も外さないことを見るため、3番手の相手を一番安くする
    odds, prob = _trio_board(lambda k: 2.0 if k == frozenset({3, 1, 5}) else 6.0 + sum(k))
    got = build_with_gate_fallback(s, PLANS[key], odds, prob, 7)
    assert got, key
    legs, stakes, used = got
    assert used.key == key and used.bet_type == "trio"
    want = [frozenset({3, 1, c}) for c in (5, 2, 7, 4)[:n]]
    assert list(legs) == want
    assert sum(stakes.values()) <= BUDGET
    # ダッチ: 賭け金 × 予測オッズ がほぼ揃う（100円単位の端数だけずれる）
    pays = [stakes[k] * odds[k] for k in legs]
    assert max(pays) - min(pays) <= 100 * max(odds[k] for k in legs)


def test_9車のB_hitは旧定義で組む():
    perms9 = list(itertools.permutations(range(1, 10), 3))
    po = {c: 60.0 for c in perms9}
    pr = {c: 1.0 / len(perms9) for c in perms9}
    shape = RaceShape("B", 1.50, 0, 0.10, True, tuple(range(1, 10)), 0.0, ())
    got = build_with_gate_fallback(shape, PLANS["B_hit"], po, pr, 9)
    assert got and got[2] is NINE_CAR_PLAN_OVERRIDES["B_hit"]


def _row(plan_key, n_entries, bet_type, stake=3400, odds=1.5):
    legs = [{"combo": f"1=2={c}", "stake": stake, "pred_odds": odds} for c in (3, 4, 5)]
    return dict(plan_key=plan_key, n_entries=n_entries, bet_type=bet_type,
                race_type="予選", axis_sum=1.5, legs=legs,
                pred_mean_payout=stake * odds, pred_min_payout=stake * odds)


@pytest.mark.parametrize("key", sorted(TRIO_HIT_PLANS))
def test_7車の三連複は払戻ゲートを当てない(key):
    import scripts.netkeirin_submit_type_lab as m
    # 平均 5,100円・全点 1.5倍＝既定ゲートなら両方の条件で落ちる
    assert m._gate_reason(_row(key, 7, "trio")) is None


@pytest.mark.parametrize("key,n,bt", [("B_hit", 9, "trifecta"), ("D_hit", 9, "trio"),
                                      ("B_hit", 7, "trifecta"), ("A_trio", 7, "trio")])
def test_それ以外には従来のゲート(key, n, bt):
    import scripts.netkeirin_submit_type_lab as m
    assert m._gate_reason(_row(key, n, bt)) is not None


def test_版が割れる(monkeypatch):
    v0 = rule_version(7)
    monkeypatch.setattr(T, "TRIO_HIT_PLANS", frozenset({"B_hit"}))
    assert rule_version(7) != v0


def test_文面は券種と一致する():
    from src.type_lab_submission import build_submission, build_title
    for key in ("B_hit", "C_hit", "D_hit"):
        assert "三連複" in build_title(key, key[0], 7), key
    assert "三連単" in build_title("B_hit", "B", 9)
    assert "三連単" in build_title("C_hit", "C", 9)
    legs = [{"combo": "3=1=5", "stake": 4000, "pred_odds": 3.0},
            {"combo": "3=1=2", "stake": 3000, "pred_odds": 4.0},
            {"combo": "3=1=7", "stake": 3000, "pred_odds": 4.0}]
    row = dict(plan_key="B_hit", type_label="B", axis1=3, axis2=1,
               p3_order=[3, 1, 5, 2, 7, 4, 6], bet_type="trio", legs=legs, n_entries=7)
    c = build_submission(row)["comment"]
    assert "買い目は三連複" in c and "三連複 3点" in c and "三連単" not in c
    row9 = dict(row, bet_type="trifecta", n_entries=9,
                legs=[{"combo": "3-1-5", "stake": 10000, "pred_odds": 6.0}])
    c9 = build_submission(row9)["comment"]
    assert "買い目は三連単" in c9 and "三連複" not in c9
