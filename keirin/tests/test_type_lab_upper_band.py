"""上帯（多穴の重ね買い）を固定する（2026-09-04）。

🔴 **いま `UPPER_BANDS` は空**（同日夕方に前向き実測で切り戻した）。仕組みそのものは
   残してあるので、ここでは `bands=(_PERM_BAND,)` を明示して機構を検査し、
   「既定では何も重ならない」ことを別に固定する。

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
    BUDGET, PLANS, ROLE_BASE, UPPER_BAND_PLANS, UPPER_BANDS,
    UPPER_BAND_N_ENTRIES, RaceShape, add_upper_band, allocate, build_legs,
    mean_expected_payout, rule_version, split_legs_by_role,
)
from src.type_lab import _PERM_BAND

#: 機構を検査するときに使う上帯（本番は空）。
BANDS = (_PERM_BAND,)
UP_TOTAL = sum(b.budget for b in BANDS)

PERMS = list(itertools.permutations(range(1, 8), 3))


def _shape(label: str = "F") -> RaceShape:
    return RaceShape(label, 1.30, 1, 0.10, False, tuple(range(1, 8)), 1.5)


def _board(high: float = 400.0, low: float = 30.0) -> tuple[dict, dict]:
    """予測オッズが low → high へ滑らかに並ぶ盤面。確率は 1/オッズ に比例。

    ⚠️ `low` は **最低2倍の床（`MIN_PAYOUT_MULT`）が置ける水準**にしてある。
       安くしすぎると `allocate` が None を返し、上帯ではなく配分の検査になる。

    🔴 **全点が同じオッズの盤面では上帯を測れない**。下帯（確率上位）と
       上帯（100-600倍の確率上位）が同じ目になり、賭け金が足されるだけで
       役割が増えない（それ自体は正しい挙動なので別のテストで固定する）。
    """
    n = len(PERMS)
    po = {c: low + (high - low) * i / (n - 1) for i, c in enumerate(PERMS)}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / po[c]) / tot for c in PERMS}
    return po, pr


def _built(plan_key: str = "F_hit", **kw):
    po, pr = _board(**kw)
    plan = PLANS[plan_key]
    legs = build_legs(_shape(plan.type_label), plan, po, pr)
    stakes = allocate(legs, po, pr, plan)
    return plan, po, pr, legs, stakes


# ───────────────────────── ① 予算の内訳 ─────────────────────────

def test_budget_is_unchanged_and_split_8_2():
    plan, po, pr, legs, stakes = _built()
    assert sum(stakes.values()) == BUDGET
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
    assert sum(st2.values()) == BUDGET, "投資は増やさない"
    base = sum(v for c, v in st2.items() if roles[c] == ROLE_BASE)
    assert base == BUDGET - UP_TOTAL == 8000
    assert set(roles.values()) <= {ROLE_BASE} | {b.kind for b in BANDS}
    assert len(legs2) == len(st2) == len(set(legs2)), "同じ目を2行に分けない"


def test_upper_legs_are_unbought_orders_of_backed_trios():
    """上帯は**下帯で2通り以上買っている3車の、まだ買っていない並び**だけ。

    🔴 ここが「惜しく外した分を拾う」の定義そのもの（2026-09-04・ユーザー判断）。
       無関係な高オッズをばらまく形に戻ると、当たっても 1.2倍のガミばかりになる。
    """
    from collections import Counter

    plan, po, pr, legs, stakes = _built()
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
    cnt = Counter(frozenset(c) for c in legs)
    upper = [c for c in legs2 if roles[c] != ROLE_BASE]
    assert upper, "上帯が乗っていません"
    for c in upper:
        assert c not in set(legs), "既に買っている目を重ねています"
        assert cnt[frozenset(c)] >= 2, "下帯が1通りしか買っていない3車を拾っています"
    assert len(upper) <= _PERM_BAND.max_legs


def test_base_legs_are_not_reselected():
    """下帯の**買い目は一切変えない**（縮むのは賭け金だけ）。"""
    plan, po, pr, legs, stakes = _built()
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
    assert [c for c in legs2 if roles[c] == ROLE_BASE] == list(legs)


def test_overlapping_leg_keeps_base_role_and_sums_stake():
    """上帯が下帯と同じ目を選んだら賭け金を足す（行は増やさない）。"""
    po = {(1, 2, 3): 150.0, (1, 3, 2): 160.0, (2, 1, 3): 170.0}
    po.update({c: 5.0 for c in PERMS if c not in po})
    pr = {c: (1.0 / po[c]) for c in PERMS}
    tot = sum(pr.values())
    pr = {c: v / tot for c, v in pr.items()}
    plan = PLANS["F_hit"]
    legs = [(1, 2, 3), (1, 3, 2)]
    stakes = allocate(legs, po, pr, plan)
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
    assert roles[(1, 2, 3)] == ROLE_BASE
    assert sum(st2.values()) == BUDGET
    assert len(legs2) == len(set(legs2))


# ───────────────────────── ③ 掛けない条件 ─────────────────────────

def test_not_applied_to_nine_cars():
    plan, po, pr, legs, stakes = _built()
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 9, BANDS)
    assert st2 == stakes and set(roles.values()) == {ROLE_BASE}
    assert UPPER_BAND_N_ENTRIES == 7


def test_not_applied_to_trio_plans():
    """三連複プランには掛けない（1商品に2券種は混ぜられない）。

    🔴 **許可リストに入っていても掛からない**ことを見る（券種の門は独立）。
    """
    import src.type_lab as tl
    po = {frozenset(c): 30.0 for c in itertools.combinations(range(1, 8), 3)}
    pr = {c: 1.0 / len(po) for c in po}
    plan = PLANS["D_hit"]
    legs = build_legs(_shape("D"), plan, po, pr)
    stakes = allocate(legs, po, pr, plan)
    orig = tl.UPPER_BAND_PLANS
    try:
        tl.UPPER_BAND_PLANS = frozenset({"D_hit"})
        legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
    finally:
        tl.UPPER_BAND_PLANS = orig
    assert st2 == stakes and set(roles.values()) == {ROLE_BASE}


def test_only_the_allowed_plans_get_the_upper_band():
    """**押さえを乗せるのは面で買う `E_hit` / `F_hit` だけ**（ユーザー決定 2026-09-04）。

    🔴 点数を絞る商品（`A_hit` 3点・`F_pay` 4点・`F_sign` 2〜3点）は
       「少ない点に厚く置いて払戻を作る」設計なので、薄い押さえを足すと
       その集中を自分で薄める。
    """
    assert UPPER_BAND_PLANS == frozenset({"E_hit", "F_hit"})
    po, pr = _board()
    for key in ("E_hit", "F_hit", "A_hit", "B_hit", "C_hit", "F_pay", "F_sign"):
        plan = PLANS[key]
        legs = build_legs(_shape(plan.type_label), plan, po, pr)
        if not legs:
            continue
        stakes = allocate(legs, po, pr, plan)
        if not stakes:
            continue
        _, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
        got = set(roles.values()) != {ROLE_BASE}
        assert got is (key in UPPER_BAND_PLANS), key


def test_falls_back_to_todays_product_when_band_is_empty():
    """上帯の条件を満たす目が1つも無いときは**今日と同じ商品**を返す。

    `_PERM_BAND` は「下帯で2通り以上買っている3車」が条件なので、
    1組合せ1通りしか買わない `A_hit`（3点）では必ず空になる。
    ⚠️ `low` は最低2倍の床が置ける水準にしてある（`_board` の注記と同じ理由）。
    """
    plan, po, pr, legs, stakes = _built("A_hit", high=80.0, low=30.0)
    legs2, st2, roles = add_upper_band(legs, stakes, plan, po, pr, 7, BANDS)
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
    import src.type_lab as tl

    from src.type_lab import build_with_gate_fallback, race_shape

    po, pr = _board()
    meta = {"race_key": "20260804_26_04", "race_date": "2026-08-04",
            "venue_name": "西武園", "race_no": 4, "race_type": "予選",
            "day_index": 2}
    # 型F（`axis_sum` 0.90 < 1.44 で混戦）になる盤面。
    p3 = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.25, 5: 0.20, 6: 0.15, 7: 0.10}
    # ⚠️ ライン番号は 1 始まりにする（0 は falsy で `_line_members` が拾わない）。
    cars = {c: dict(p3=p3[c], pw=p3[c] / 2, line_group=(c - 1) // 3 + 1,
                    line_pos=(c - 1) % 3 + 1, style="逃", race_point=100 - c,
                    behind=0) for c in range(1, 8)}
    # 🔴 比較の基準は**同じ盤面から**作る（`race_shape` を通さないと並びが違う）。
    shape = race_shape({c: v["p3"] for c, v in cars.items()},
                       {c: v["line_group"] for c, v in cars.items()},
                       {c: v["line_pos"] for c, v in cars.items()},
                       {c: v["style"] for c, v in cars.items()},
                       {c: v["race_point"] for c, v in cars.items()},
                       {c: v["behind"] for c, v in cars.items()},
                       meta["day_index"],
                       {c: v["pw"] for c, v in cars.items()})
    assert shape.type_label == "F", shape.type_label
    # 🔴 `F_hit` は平均想定払戻ゲートに落ちると帯15倍へ切り替わる（`GATE_FALLBACK`）。
    #    基準もその関数を通す（切り替わっても `key` は `F_hit` のまま＝許可リストに乗る）。
    _, base_stakes, used = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, 7)
    assert used.key == "F_hit"
    before = mean_expected_payout(base_stakes, po)

    # 🔴 本番の `UPPER_BANDS` は空なので、**機構を見るために一時的に有効化**する。
    orig = tl.UPPER_BANDS
    try:
        tl.UPPER_BANDS = BANDS
        rows = B.rows_for_race(meta, cars, po, pr, "paper")
    finally:
        tl.UPPER_BANDS = orig
    row = next((r for r in rows if r["plan_key"] == "F_hit"), None)
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


def test_bands_are_read_at_call_time():
    """定数を差し替えたら**その場で**効くこと（引数の既定値に束縛しない）。

    🔴 `def add_upper_band(..., bands=UPPER_BANDS)` と書くと def 時に束縛され、
       定数を差し替えても古い値のまま動く。検証スクリプトで「上帯なし」の
       対照を作ったときに実際に踏んだ（対照が対照になっていなかった）。
    """
    import src.type_lab as tl
    plan, po, pr, legs, stakes = _built()
    _, st_off, roles_off = add_upper_band(legs, stakes, plan, po, pr, 7)
    assert st_off == stakes and set(roles_off.values()) == {ROLE_BASE}, (
        "本番の `UPPER_BANDS` は空のはず（2026-09-04 に切り戻し）")
    orig = tl.UPPER_BANDS
    try:
        tl.UPPER_BANDS = BANDS
        _, st_on, roles_on = add_upper_band(legs, stakes, plan, po, pr, 7)
        assert set(roles_on.values()) != {ROLE_BASE}
    finally:
        tl.UPPER_BANDS = orig


def test_upper_band_is_off_in_production():
    """🔴 **いまは重ね買いをしない**（2026-09-04 夕方・前向き実測で切り戻し）。

    9/1〜9/4 の95行で反実仮想を採点したところ、上帯で拾えたのは1件だけで
    元から当たっていた30件が2割減し、ROI 80.1 → 66.6% だった。
    戻すときは `UPPER_BANDS = (_PERM_BAND,)` の1行。
    """
    assert UPPER_BANDS == ()
