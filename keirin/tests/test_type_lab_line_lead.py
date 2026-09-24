"""逃げ先頭ライン（`L_lead`・2026-09-24 新設・検証中）。

ユーザー決定（2026-09-24）: 「型ラボに新ランクを追加」→ 同日「1日上限5本・穴狙いとして・
現在売っていないレースに追加」「モーニングを除外として、早い未販売の5レース」。
ここで固定するのは次の4つ（売る段そのものは `test_type_lab_line_lead_sell.py`）:

- **型ラボの本体の経路では売らない**こと（`sell_plans_for` がどの型・車数・種別・引数でも
  返さず、`SELLABLE_PLAN_KEYS` にも無い）。入れると `L_lead` で出したレースが
  「型ラボ自身が取ったレース」扱いになり、1日5本の上限もモーニング除外も効かなくなる
- **生成はされる**こと（7車の `plans_for` に入り、条件を満たすレースでだけ組める）
- **既存の版（`rule_version`）を割らない**こと（`L_lead` の行は別の版を持つ）
- 条件の境界（`line_lead_legs` の docstring）と 1万円の均等配分
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import src.type_lab as tl

P3 = {1: .9, 2: .5, 3: .6, 4: .4, 5: .3, 6: .2, 7: .1}
RP = {1: 100.0, 2: 95.0, 3: 94.0, 4: 93.0, 5: 80.0, 6: 79.0, 7: 70.0}
ST = {1: "逃", 2: "追", 3: "追", 4: "両", 5: "逃", 6: "追", 7: "追"}
LINES = ((1, 2, 3), (5, 6), (4, 7))


# ───────────────────────── 型ラボの本体の経路では売らない ─────────────────────────

@pytest.mark.parametrize("n", [7, 9])
@pytest.mark.parametrize("label", list("ABCDEF"))
@pytest.mark.parametrize("race_type", [None, "決勝", "準決勝", "特選", "一般", "予選"])
def test_型ラボの本体では売らない(label, n, race_type):
    for kw in ({}, {"pw_ent": 2.0, "trio_ok": True},
               {"axis_sum": 1.30, "one_axis_ok": True}, {"axis_sum": 1.60}):
        keys = {p.key for p in tl.sell_plans_for(label, n, race_type, **kw)}
        assert not keys & tl.LINE_LEAD_PLAN_KEYS, (label, n, race_type, kw, keys)


def test_入稿スクリプトの売れるプランに無い():
    assert not tl.LINE_LEAD_PLAN_KEYS & tl.SELLABLE_PLAN_KEYS
    assert not tl.LINE_LEAD_PLAN_KEYS & set(tl.SELL_PLANS)
    assert not tl.LINE_LEAD_PLAN_KEYS & tl.HIGHPAY_PLAN_KEYS


# ───────────────────────────── 生成はする ─────────────────────────────

@pytest.mark.parametrize("label", list("ABCDEF"))
def test_7車は型に関係なく組む(label):
    assert "L_lead" in [p.key for p in tl.plans_for(label, 7)]


@pytest.mark.parametrize("label", list("ABCDEF"))
def test_9車では組まない(label):
    assert "L_lead" not in [p.key for p in tl.plans_for(label, 9)]


def _shape(lead_legs):
    return tl.RaceShape("F", 1.3, 1, 0.0, False, (1, 2, 3, 4, 5, 6, 7),
                        lead_legs=tuple(lead_legs))


def test_条件を満たさないレースでは行を作らない():
    assert tl.build_legs(_shape(()), tl.PLANS["L_lead"], {}, {}) is None


def test_予測オッズの無い目は外す():
    legs = [(5, 6, 1), (5, 6, 2)]
    got = tl.build_legs(_shape(legs), tl.PLANS["L_lead"], {(5, 6, 1): 30.0}, {})
    assert got == [(5, 6, 1)]


def test_race_shapeが買い目を持つ():
    lg = {1: 1, 2: 1, 3: 1, 5: 2, 6: 2, 4: 3, 7: 3}
    lp = {1: 1, 2: 2, 3: 3, 5: 1, 6: 2, 4: 1, 7: 2}
    sh = tl.race_shape(P3, lg, lp, ST, RP, {c: 0.0 for c in P3}, 2)
    assert sh.lead_legs == ((5, 6, 1), (5, 6, 2), (5, 6, 3), (5, 6, 4))


# ───────────────────────────── 条件の境界 ─────────────────────────────

def test_基本形():
    assert tl.line_lead_legs(P3, LINES, ST, RP) == (
        (5, 6, 1), (5, 6, 2), (5, 6, 3), (5, 6, 4))


def test_得点1位のラインが4車なら組まない():
    assert tl.line_lead_legs(P3, ((1, 2, 3, 4), (5, 6)), ST, RP) == ()


def test_得点1位が単騎でも組む():
    lines = ((2, 3), (5, 6), (4, 7))
    assert tl.line_lead_legs(P3, lines, ST, RP)[0][:2] == (5, 6)


def test_先頭が逃でなければ組まない():
    assert tl.line_lead_legs(P3, LINES, {**ST, 5: "両"}, RP) == ()


def test_先頭の得点が4位以上なら組まない():
    rp = {**RP, 5: 93.5}      # 5番が得点4位
    assert tl.line_lead_legs(P3, LINES, ST, rp) == ()


def test_第三のラインの番手は3着から外すが先頭は残す():
    thirds = [c for _, _, c in tl.line_lead_legs(P3, LINES, ST, RP)]
    assert 4 in thirds          # 第三のラインの先頭
    assert 7 not in thirds      # 第三のラインの番手（しかも最下位）


def test_3着内率の最下位は3着から外す():
    p3 = {**P3, 1: 0.05, 7: 0.95}     # 1番（得点1位のライン）が3着内率最下位
    thirds = [c for _, _, c in tl.line_lead_legs(p3, LINES, ST, RP)]
    assert 1 not in thirds


def test_未格付けの選手は得点順位から外す():
    # 7番（第三のラインの番手）を未格付けにしても、5番の得点順位は 5位のまま
    rp = {**RP, 7: 0.0}
    assert tl.line_lead_legs(P3, LINES, ST, rp)[0][:2] == (5, 6)
    # 先頭が未格付けなら、得点順位が付かないので組まない
    rp2 = {**RP, 5: 0.0}
    assert tl.line_lead_legs(P3, LINES, ST, rp2) == ()


def test_7車以外は組まない():
    p3 = {c: v for c, v in P3.items() if c != 7}
    assert tl.line_lead_legs(p3, ((1, 2, 3), (5, 6)), ST, RP) == ()


# ───────────────────────────── 配分 ─────────────────────────────

@pytest.mark.parametrize("k,each", [(3, 3300), (4, 2500), (6, 1600), (9, 1100)])
def test_1万円を均等に割り余りは配らない(k, each):
    legs = [(5, 6, c) for c in range(1, k + 1)]
    st = tl.allocate(legs, {c: 30.0 for c in legs}, {}, tl.PLANS["L_lead"])
    assert set(st.values()) == {each}
    assert sum(st.values()) <= tl.BUDGET


# ───────────────────────────── 版 ─────────────────────────────

def test_L_leadを動かしても既存の版は割れない(monkeypatch):
    before = (tl.rule_version(7), tl.rule_version(9))
    ll = tl.line_lead_rule_version()
    monkeypatch.setitem(tl.PLANS, "L_lead", replace(tl.PLANS["L_lead"], alloc="dutch"))
    assert (tl.rule_version(7), tl.rule_version(9)) == before
    assert tl.line_lead_rule_version() != ll


def test_L_leadの条件を動かすと専用の版が割れる(monkeypatch):
    ll = tl.line_lead_rule_version()
    monkeypatch.setattr(tl, "LINE_LEAD_LEAD_RP_RANK_MIN", 4)
    assert tl.line_lead_rule_version() != ll
    assert ll.startswith("L") and len(ll) == 12
