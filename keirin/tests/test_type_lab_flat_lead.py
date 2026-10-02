"""混戦の逃げ先頭ライン（`L_flat`・2026-10-02 新設・**紙上の検証だけ**）。

ユーザー決定（2026-10-02）: 「紙上で検証を実施」。事前登録は
`docs/type_lab/flat_lead_2026_10_02.md`。ここで固定するのは次の5つ:

- **どの経路でも売らない**こと（`sell_plans_for`・`SELLABLE_PLAN_KEYS`・高額枠に無く、
  `L_lead` を売る段が読む `LINE_LEAD_PLAN_KEYS` にも入らない）
- **生成はされる**こと（7車の `plans_for` に入り、混戦のレースでだけ組める）
- 混戦の境界（`axis_sum < FLAT_LEAD_AXIS_SUM_MAX`）と、先頭の得点順位の条件が無いこと
- **既存の版（`rule_version` / `line_lead_rule_version`）を割らない**こと
- 1万円の均等配分（`L_lead` と同じ）
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import src.type_lab as tl

P3 = {1: .9, 2: .5, 3: .6, 4: .4, 5: .3, 6: .2, 7: .1}
RP = {1: 100.0, 2: 95.0, 3: 94.0, 4: 93.0, 5: 80.0, 6: 79.0, 7: 70.0}
ST = {1: "逃", 2: "追", 3: "追", 4: "両", 5: "逃", 6: "追", 7: "追"}
LINES = ((1, 2, 3), (5, 6), (4, 7))
FLAT = tl.FLAT_LEAD_AXIS_SUM_MAX - 0.01


# ───────────────────────────── 売らない ─────────────────────────────

@pytest.mark.parametrize("n", [7, 9])
@pytest.mark.parametrize("label", list("ABCDEF"))
@pytest.mark.parametrize("race_type", [None, "決勝", "準決勝", "特選", "一般", "予選"])
def test_型ラボの本体では売らない(label, n, race_type):
    for kw in ({}, {"pw_ent": 2.0, "trio_ok": True},
               {"axis_sum": 1.30, "one_axis_ok": True}, {"axis_sum": 1.60}):
        keys = {p.key for p in tl.sell_plans_for(label, n, race_type, **kw)}
        assert not keys & tl.FLAT_LEAD_PLAN_KEYS, (label, n, race_type, kw, keys)


def test_売れるプランにもL_leadの販売段にも無い():
    assert not tl.FLAT_LEAD_PLAN_KEYS & tl.SELLABLE_PLAN_KEYS
    assert not tl.FLAT_LEAD_PLAN_KEYS & set(tl.SELL_PLANS)
    assert not tl.FLAT_LEAD_PLAN_KEYS & tl.HIGHPAY_PLAN_KEYS
    # 🔴 `L_lead` を売る段（`netkeirin_submit_type_lab._load_line_lead_rows`）はこの集合で拾う
    assert not tl.FLAT_LEAD_PLAN_KEYS & tl.LINE_LEAD_PLAN_KEYS


# ───────────────────────────── 生成はする ─────────────────────────────

@pytest.mark.parametrize("label", list("ABCDEF"))
def test_7車は型に関係なく組む(label):
    assert "L_flat" in [p.key for p in tl.plans_for(label, 7)]


@pytest.mark.parametrize("label", list("ABCDEF"))
def test_9車では組まない(label):
    assert "L_flat" not in [p.key for p in tl.plans_for(label, 9)]


def _shape(flat_legs):
    return tl.RaceShape("F", 1.3, 1, 0.0, False, (1, 2, 3, 4, 5, 6, 7),
                        flat_legs=tuple(flat_legs))


def test_条件を満たさないレースでは行を作らない():
    assert tl.build_legs(_shape(()), tl.PLANS["L_flat"], {}, {}) is None


def test_予測オッズの無い目は外す():
    legs = [(5, 6, 1), (5, 6, 2)]
    got = tl.build_legs(_shape(legs), tl.PLANS["L_flat"], {(5, 6, 1): 30.0}, {})
    assert got == [(5, 6, 1)]


def test_L_leadの買い目とは別に持つ():
    sh = tl.RaceShape("F", 1.3, 1, 0.0, False, (1, 2, 3, 4, 5, 6, 7),
                      lead_legs=((5, 6, 1),), flat_legs=((3, 4, 1),))
    odds = {(5, 6, 1): 30.0, (3, 4, 1): 30.0}
    assert tl.build_legs(sh, tl.PLANS["L_flat"], odds, {}) == [(3, 4, 1)]
    assert tl.build_legs(sh, tl.PLANS["L_lead"], odds, {}) == [(5, 6, 1)]


def _race_shape(p3):
    lg = {1: 1, 2: 1, 3: 1, 5: 2, 6: 2, 4: 3, 7: 3}
    lp = {1: 1, 2: 2, 3: 3, 5: 1, 6: 2, 4: 1, 7: 2}
    return tl.race_shape(p3, lg, lp, ST, RP, {c: 0.0 for c in p3}, 2)


def test_race_shapeは混戦のときだけ買い目を持つ():
    # 軸2車（3着内率の上位2車）の合計が 0.64+0.66=1.30 < 1.327
    flat = _race_shape({**P3, 1: .66, 3: .64, 2: .5})
    assert flat.axis_sum < tl.FLAT_LEAD_AXIS_SUM_MAX
    assert flat.flat_legs and all(leg[:2] == (5, 6) for leg in flat.flat_legs)
    firm = _race_shape(P3)   # 0.9+0.6=1.5
    assert firm.axis_sum >= tl.FLAT_LEAD_AXIS_SUM_MAX
    assert firm.flat_legs == ()


# ───────────────────────────── 条件の境界 ─────────────────────────────

def test_混戦の境界は未満():
    assert tl.flat_lead_legs(P3, LINES, ST, RP, FLAT)
    assert tl.flat_lead_legs(P3, LINES, ST, RP, tl.FLAT_LEAD_AXIS_SUM_MAX) == ()


def test_先頭の得点順位は問わない():
    rp = {**RP, 5: 99.0}      # 5番が得点2位 → L_lead は組まないが L_flat は組む
    assert tl.line_lead_legs(P3, LINES, ST, rp) == ()
    assert tl.flat_lead_legs(P3, LINES, ST, rp, FLAT)[0][:2] == (5, 6)


def test_それ以外の条件はL_leadと同じ():
    # 先頭が逃でない
    assert tl.flat_lead_legs(P3, LINES, {**ST, 5: "両"}, RP, FLAT) == ()
    # 得点1位のラインが4車
    assert tl.flat_lead_legs(P3, ((1, 2, 3, 4), (5, 6)), ST, RP, FLAT) == ()
    # 3着の除外（第三のラインの番手・最下位）は同じ
    assert tl.flat_lead_legs(P3, LINES, ST, RP, FLAT) == tl.line_lead_legs(P3, LINES, ST, RP)


def test_L_leadの定数を差し替えるとline_lead_legsに効く(monkeypatch):
    """🔴 既定値を定義時に束縛しない（検証スクリプトが定数を差し替えて使う）。"""
    monkeypatch.setattr(tl, "LINE_LEAD_LEAD_RP_RANK_MIN", 1)
    rp = {**RP, 5: 99.0}
    assert tl.line_lead_legs(P3, LINES, ST, rp)[0][:2] == (5, 6)


# ───────────────────────────── 配分 ─────────────────────────────

@pytest.mark.parametrize("k,each", [(3, 3300), (4, 2500), (6, 1600), (9, 1100)])
def test_1万円を均等に割り余りは配らない(k, each):
    legs = [(5, 6, c) for c in range(1, k + 1)]
    st = tl.allocate(legs, {c: 30.0 for c in legs}, {}, tl.PLANS["L_flat"])
    assert set(st.values()) == {each}
    assert sum(st.values()) <= tl.BUDGET


def test_上帯は重ねない():
    legs = [(5, 6, c) for c in (1, 2, 3, 4)]
    odds = {c: 30.0 for c in legs}
    st = tl.allocate(legs, odds, {}, tl.PLANS["L_flat"])
    legs2, st2, _ = tl.add_upper_band(legs, st, tl.PLANS["L_flat"], odds, {}, 7)
    assert list(legs2) == legs and st2 == st


# ───────────────────────────── 版 ─────────────────────────────

def test_L_flatを動かしても既存の版は割れない(monkeypatch):
    before = (tl.rule_version(7), tl.rule_version(9), tl.line_lead_rule_version())
    fl = tl.flat_lead_rule_version()
    monkeypatch.setitem(tl.PLANS, "L_flat", replace(tl.PLANS["L_flat"], alloc="dutch"))
    assert (tl.rule_version(7), tl.rule_version(9), tl.line_lead_rule_version()) == before
    assert tl.flat_lead_rule_version() != fl


def test_L_flatの条件を動かすと専用の版が割れる(monkeypatch):
    fl = tl.flat_lead_rule_version()
    monkeypatch.setattr(tl, "FLAT_LEAD_AXIS_SUM_MAX", 1.30)
    assert tl.flat_lead_rule_version() != fl
    assert fl.startswith("F") and len(fl) == 12
