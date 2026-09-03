"""上帯（多穴の重ね買い）を固定する（2026-09-04）。

予算 10,000円のうち 2割を高オッズの目へ回すバーベル。**商品も投資も増やさない**。
実測は `docs/type_lab/overlay_upper_band_2026_09_04.md`。

🔴 ここで固定するのは「**静かに別物にならない**」ための3点:
   ① 予算の内訳（下帯8割・上帯2割）と 1レース1商品
   ② **入稿ゲートの母集団が変わらないこと**（閾値を下帯の予算比で割り戻す）
   ③ 掛けない条件（9車・三連複・組めない）では**今日と同じ商品**が返ること
"""
from __future__ import annotations

import itertools

from src.type_lab import (
    BUDGET, PLANS, ROLE_BASE, UPPER_BANDS, UPPER_BAND_N_ENTRIES,
    UPPER_BAND_TOTAL, RaceShape, add_upper_band, allocate, build_legs,
    mean_expected_payout, rule_version, split_legs_by_role,
)

PERMS = list(itertools.permutations(range(1, 8), 3))


def _shape(label: str = "C") -> RaceShape:
    return RaceShape(label, 1.30, 1, 0.10, False, tuple(range(1, 8)), 1.5)


def _board(high: float = 400.0, low: float = 12.0) -> tuple[dict, dict]:
    """予測オッズが low → high へ滑らかに並ぶ盤面。確率は 1/オッズ に比例。

    🔴 **全点が同じオッズの盤面では上帯を測れない**。下帯（確率上位）と
       上帯（100-600倍の確率上位）が同じ目になり、賭け金が足されるだけで
       役割が増えない（それ自体は正しい挙動なので別のテストで固定する）。
    """
    n = len(PERMS)
    po = {c: low + (high - low) * i / (n - 1) for i, c in enumerate(PERMS)}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / po[c]) / tot for c in PERMS}
    return po, pr


def _built(plan_key: str = "C_hit", **kw):
    po, pr = _board(**kw)
    plan = PLANS[plan_key]
    legs = build_legs(_shape(plan.type_label), plan, po, pr)
    stakes = allocate(legs, po, pr, plan)
    return plan, po, pr, legs, stakes


# ───────────────────────── ① 予算の内訳 ─────────────────────────

def test_budget_is_unchanged_and_split_8_2():
    plan, po, pr, legs, stakes = _built()
    assert sum(stakes.values()) == BUDGET
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert sum(st2.values()) == BUDGET, "投資は増やさない"
    base = sum(v for c, v in st2.items() if roles[c] == ROLE_BASE)
    assert base == BUDGET - UPPER_BAND_TOTAL == 8000
    assert set(roles.values()) <= {ROLE_BASE, "band", "sign"}
    assert len(legs2) == len(st2) == len(set(legs2)), "同じ目を2行に分けない"


def test_upper_legs_stay_in_their_odds_band():
    """`band` は 100-600倍・`sign` は 600倍以下（帯ROI が崩れる上を買わない）。"""
    plan, po, pr, legs, stakes = _built()
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7)
    for c, role in roles.items():
        if role == "band":
            assert 100.0 <= po[c] <= 600.0
        elif role == "sign":
            assert po[c] <= 600.0


def test_base_legs_are_not_reselected():
    """下帯の**買い目は一切変えない**（縮むのは賭け金だけ）。"""
    plan, po, pr, legs, stakes = _built()
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert [c for c in legs2 if roles[c] == ROLE_BASE] == list(legs)


def test_overlapping_leg_keeps_base_role_and_sums_stake():
    """上帯が下帯と同じ目を選んだら賭け金を足す（行は増やさない）。"""
    po = {(1, 2, 3): 150.0, (1, 3, 2): 160.0, (2, 1, 3): 170.0}
    po.update({c: 5.0 for c in PERMS if c not in po})
    pr = {c: (1.0 / po[c]) for c in PERMS}
    tot = sum(pr.values())
    pr = {c: v / tot for c, v in pr.items()}
    plan = PLANS["C_hit"]
    legs = [(1, 2, 3), (1, 3, 2)]
    stakes = allocate(legs, po, pr, plan)
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert roles[(1, 2, 3)] == ROLE_BASE
    assert sum(st2.values()) == BUDGET
    assert len(legs2) == len(set(legs2))


# ───────────────────────── ③ 掛けない条件 ─────────────────────────

def test_not_applied_to_nine_cars():
    plan, po, pr, legs, stakes = _built()
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 9)
    assert st2 == stakes and set(roles.values()) == {ROLE_BASE}
    assert UPPER_BAND_N_ENTRIES == 7


def test_not_applied_to_trio_plans():
    """三連複プラン（`D_hit` / `A_trio`）には掛けない（1商品に2券種は混ぜられない）。"""
    po = {frozenset(c): 30.0 for c in itertools.combinations(range(1, 8), 3)}
    pr = {c: 1.0 / len(po) for c in po}
    plan = PLANS["D_hit"]
    legs = build_legs(_shape("D"), plan, po, pr)
    stakes = allocate(legs, po, pr, plan)
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert st2 == stakes and set(roles.values()) == {ROLE_BASE}


def test_falls_back_to_todays_product_when_band_is_empty():
    """100倍以上の目が1つも無い盤面では**今日と同じ商品**を返す。"""
    plan, po, pr, legs, stakes = _built(high=40.0, low=16.0)
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert st2 == stakes and set(roles.values()) == {ROLE_BASE}


# ───────────────────────── ② 入稿ゲート ─────────────────────────

def test_gate_value_is_recorded_before_the_upper_band():
    """`pred_mean_payout` は**上帯を重ねる前**の値（＝今日と同じ）で記録される。

    🔴 これが入稿ゲート `netkeirin_submit_type_lab._gate_reason` の入力。
       重ねた後の値にすると、上帯（100-600倍）が平均を押し上げて
       **ゲートが事実上無効**になり、今は落ちているレースが静かに通る。
    🔴 「重ねた後の下帯だけ」で出すのも誤り。上帯が下帯と同じ目を選ぶと
       賭け金が合算されてその1点の想定払戻が跳ね、平均を歪める。
    """
    import json

    import scripts.build_type_lab_picks as B

    po, pr = _board()
    plan = PLANS["A_hit"]
    legs = build_legs(_shape("A"), plan, po, pr)
    stakes = allocate(legs, po, pr, plan)
    before = mean_expected_payout(stakes, po)

    meta = {"race_key": "20260804_26_04", "race_date": "2026-08-04",
            "venue_name": "西武園", "race_no": 4, "race_type": "予選",
            "day_index": 1}
    # 型A（`axis_sum` 1.50 >= 1.44 で堅い）になる盤面。
    p3 = {1: 0.80, 2: 0.70, 3: 0.30, 4: 0.25, 5: 0.20, 6: 0.15, 7: 0.10}
    cars = {c: dict(p3=p3[c], pw=p3[c] / 2, line_group=(c - 1) // 3,
                    line_pos=(c - 1) % 3, style="逃", race_point=100 - c,
                    behind=0) for c in range(1, 8)}
    rows = B.rows_for_race(meta, cars, po, pr, "paper")
    row = next((r for r in rows if r["plan_key"] == "A_hit"), None)
    assert row is not None, [r["plan_key"] for r in rows]
    got = json.loads(row["legs"])
    base = [lg for lg in got if lg.get("role", ROLE_BASE) == ROLE_BASE]
    upper = [lg for lg in got if lg.get("role", ROLE_BASE) != ROLE_BASE]
    assert upper, "上帯が乗っていない"
    assert sum(lg["stake"] for lg in got) == BUDGET
    # ゲート値は上帯を重ねる前の平均（合成した後の値ではない）
    assert abs(row["pred_mean_payout"] - before) < 1.0
    after = sum(lg["stake"] * lg["pred_odds"] for lg in base) / len(base)
    assert row["pred_mean_payout"] != round(after, 1), (
        "重ねた後の下帯から出している（ゲートの母集団が動く）")
    assert row["n_legs"] == len(got)


def test_rule_version_tracks_the_upper_band():
    """上帯を動かしたら版が割れる（新旧の行が混ざらない）。9車は影響しない。"""
    import src.type_lab as tl
    before7, before9 = rule_version(7), rule_version(9)
    orig = tl.UPPER_BANDS
    try:
        tl.UPPER_BANDS = (tl.UpperBand("band", 2_000, "prob_top",
                                       min_odds=100.0, max_odds=600.0, max_legs=8),)
        assert rule_version(7) != before7
        assert rule_version(9) == before9
    finally:
        tl.UPPER_BANDS = orig
    assert rule_version(7) == before7


def test_split_legs_by_role_defaults_to_base():
    """役割の無い行（2026-09-04 より前）は全部 下帯として読む。"""
    old = [{"combo": "1-2-3"}, {"combo": "1-3-2"}]
    assert split_legs_by_role(old) == old
    assert len(UPPER_BANDS) == 2


def test_bands_are_read_at_call_time():
    """定数を差し替えたら**その場で**効くこと（引数の既定値に束縛しない）。

    🔴 `def add_upper_band(..., bands=UPPER_BANDS)` と書くと def 時に束縛され、
       定数を差し替えても古い値のまま動く。検証スクリプトで「上帯なし」の
       対照を作ったときに実際に踏んだ（対照が対照になっていなかった）。
    """
    import src.type_lab as tl
    plan, po, pr, legs, stakes = _built()
    orig = tl.UPPER_BANDS
    try:
        tl.UPPER_BANDS = ()
        _, st_off, roles_off = add_upper_band(legs, stakes, plan, po, pr, 7)
        assert st_off == stakes and set(roles_off.values()) == {ROLE_BASE}
    finally:
        tl.UPPER_BANDS = orig
    _, st_on, roles_on = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert set(roles_on.values()) != {ROLE_BASE}
