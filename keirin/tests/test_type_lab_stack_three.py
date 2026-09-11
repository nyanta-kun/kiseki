"""2026-09-11 に入れた3機構の**適用範囲と不変条件**を固定する。

🔴 3つとも「掛ける対象を表から消すと静かに効かなくなる」型なので、
   範囲そのものをテストで縛る（`AXIS_GATE_EXEMPT_PLANS` と同じ思想）。
"""
import itertools

from src.strategy_wt import (RANK_7T3_LINE_ADJ_REV_W, rank_7t3_blend_probs,
                            rank_7t3_order_swap_probs)
from src.type_lab import (MIN_MEAN_PAYOUT, OSAE_MAX_LEGS, OSAE_MIN_PRED_ODDS,
                          OSAE_PLANS, OSAE_STAKE, ORDER_SWAP_PLANS, PLANS,
                          RaceShape, TAU_ADAPTIVE_MAX_LEGS, apply_order_swap,
                          apply_osae, mean_expected_payout)

CARS = [1, 2, 3, 4, 5, 6, 7]
LG = {1: "A", 2: "A", 3: "A", 4: "B", 5: "B", 6: "C", 7: "C"}
LP = {1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 1, 7: 2}
PW = {1: .30, 2: .22, 3: .15, 4: .12, 5: .10, 6: .06, 7: .05}
P3 = {1: .70, 2: .60, 3: .50, 4: .45, 5: .35, 6: .25, 7: .15}


def _shape():
    return RaceShape("F", 1.30, 1, 0.2, False, tuple(CARS))


# ───────────────────────── ① τ適応 ─────────────────────────

def test_tau_adaptive_is_only_on_c_hit():
    """🔴 τ適応は `C_hit` だけ。広げるなら測り直すこと（9車は未測定）。"""
    assert [k for k, v in PLANS.items() if v.tau_adaptive] == ["C_hit"]


def test_tau_adaptive_upper_bound_is_the_dial_the_user_chose():
    """🔴 上限は「表示的中 ↔ 払戻中央」のダイヤル。2026-09-11 にユーザーが 12 を選択。

    値を動かすと取引条件が変わる（16 なら +3.65pt / 払戻中央 −7.6%）ので、
    **勝手に動かさない**ことを固定する。
    """
    assert TAU_ADAPTIVE_MAX_LEGS == 12


# ───────────────────────── ② λr 並べ替え ─────────────────────────

def test_order_swap_is_only_on_plans_where_the_band_is_loose():
    """🔴 帯順守で両窓とも効くのは `B_hit`（帯なし）と `F_hit`（帯5倍）だけ。

    `C_hit`(15倍+) / `E_hit`(30倍+) は帯が先に順序の選択肢を潰していて効かない
    （+0.06/−0.09・+0.12/−0.12）。`F_sign` は効くが**一撃商品**なので入れない
    （KPI は払戻中央と 10万+・DESIGN 2.1）。
    """
    assert ORDER_SWAP_PLANS == {"B_hit", "F_hit"}


def test_reverse_bonus_lifts_the_deputy_passing_the_leader():
    """🟢 λr は「番手が先頭を差す」並びの確率を上げる（順方向は据え置き）。"""
    a = rank_7t3_blend_probs(CARS, PW, P3, line_group=LG, line_pos=LP)
    b = rank_7t3_order_swap_probs(CARS, PW, P3, line_group=LG, line_pos=LP)
    assert RANK_7T3_LINE_ADJ_REV_W[0] > 1.0
    assert b[(2, 1, 3)] > a[(2, 1, 3)], "番手→先頭が上がっていない"
    assert abs(sum(b.values()) - 1.0) < 1e-9, "正規化が壊れている"


def test_order_swap_never_leaves_the_band_or_drops_below_min_point_odds():
    """🔴 帯の外・2.0倍未満へは移さない（`DESIGN.md` 2.4 の「価格の道具」を守る）。"""
    plan = PLANS["F_hit"]
    pr = rank_7t3_blend_probs(CARS, PW, P3, line_group=LG, line_pos=LP)
    op = rank_7t3_order_swap_probs(CARS, PW, P3, line_group=LG, line_pos=LP)
    po = {c: max(2.5, 0.75 / max(v, 1e-9)) for c, v in pr.items()}
    legs = sorted(pr, key=lambda k: -pr[k])[:8]
    nl, _ = apply_order_swap(plan, legs, {c: 1200 for c in legs}, po, op)
    assert len(nl) == len(legs), "点数が動いている"
    assert len(set(nl)) == len(nl), "同じ目を2点買っている"
    for c in nl:
        assert float(po[c]) >= max(float(plan.min_odds), 2.0)
        assert sorted(set(c)) in [sorted(set(x)) for x in legs], "3車の集合が変わっている"


def test_order_swap_is_a_no_op_without_order_probs():
    """⚠️ `order_probs` が無ければ何もしない（後方互換・欠測は素通し）。"""
    plan = PLANS["F_hit"]
    legs = [(1, 2, 3), (1, 3, 2)]
    st = {c: 5000 for c in legs}
    assert apply_order_swap(plan, legs, st, {}, None) == (legs, st)


# ───────────────────────── ③ 押さえ目 ─────────────────────────

def test_osae_never_touches_one_shot_products_or_trio():
    """🔴 一撃商品（`A_ana` / `*_sign` / `*_big`）と三連複には足さない。

    一撃商品は表示的中こそ上がるが、KPI である払戻中央を 3〜5% 削る（DESIGN 2.1）。
    """
    # 🔴 2026-09-11: ユーザー判断で**空＝無効（様子見）**。確認窓で 0 と区別できず
    #    （McNemar p=0.125）、ガミが +1〜7件増えるため。機構は残してある。
    #    戻すときも一撃商品と三連複は**入れないこと**。
    for key in ("A_ana", "F_sign", "F_pay", "A_trio", "D_hit", "F_line"):
        assert key not in OSAE_PLANS, key
    assert OSAE_PLANS <= {"A_hit", "B_hit", "C_hit", "E_hit", "F_hit"}


def test_osae_only_adds_long_shots_at_a_flat_stake():
    """🟢 押さえは **軸2車を1-2着に置き、3着が予測125倍+** の目を固定100円で最大2点。

    🔴 下限を外すとガミ率が倍（3.36→6.99%）になる。100円が表示的中になるのは
       確定100倍超のときだけなので、この下限は**予算1万円から導かれる構造的な条件**。
    """
    assert OSAE_MIN_PRED_ODDS >= 100.0, "100倍を割ると当たってもガミになる"
    assert OSAE_STAKE * OSAE_MIN_PRED_ODDS >= 10_000, "押さえが予算を超えられない"
    shape = _shape()
    plan = PLANS["F_hit"]
    if plan.key not in OSAE_PLANS:
        import pytest
        pytest.skip("押さえ目は現在無効（様子見）。機構の検査は戻したときに有効化する")
    po = {c: 30.0 for c in itertools.permutations(CARS, 3)}
    po[(1, 2, 7)] = 400.0
    po[(2, 1, 7)] = 300.0
    pr = {c: 1.0 / v for c, v in po.items()}
    tot = sum(pr.values())
    pr = {c: v / tot for c, v in pr.items()}
    legs = [(1, 2, 3), (1, 2, 4), (2, 1, 3), (2, 1, 4)]
    st = {c: 2500 for c in legs}
    nl, ns = apply_osae(shape, plan, legs, st, po, pr)
    add = [c for c in nl if c not in legs]
    assert 0 < len(add) <= OSAE_MAX_LEGS
    for c in add:
        assert ns[c] == OSAE_STAKE, "押さえが床に乗って固定額でなくなっている"
        assert c[0] in (1, 2) and c[1] in (1, 2), "軸2車を1-2着に置いていない"
        assert float(po[c]) >= OSAE_MIN_PRED_ODDS
    assert sum(ns.values()) <= 10_000, "予算を超えている"


def test_osae_backs_off_when_it_would_break_the_gate():
    """🔴 ゲートを割るなら押さえない＝**在庫を1件も減らさない**。

    発端の 2026-09-10 京王閣7R がこれ（平均想定払戻 20,690円 とゲートの際で、
    押さえ2点を足すと 19,629円 に落ちる）。
    """
    shape = _shape()
    plan = PLANS["F_hit"]
    if plan.key not in OSAE_PLANS:
        import pytest
        pytest.skip("押さえ目は現在無効（様子見）")
    # 全点が安く、平均想定払戻がゲートのすぐ上しかない板
    po = {c: 2.05 for c in itertools.permutations(CARS, 3)}
    po[(1, 2, 7)] = 400.0
    pr = {c: 1.0 / v for c, v in po.items()}
    tot = sum(pr.values())
    pr = {c: v / tot for c, v in pr.items()}
    legs = [(1, 2, 3), (2, 1, 3)]
    st = {c: 5000 for c in legs}
    assert mean_expected_payout(st, po) <= MIN_MEAN_PAYOUT * 1.5, "前提が崩れている"
    nl, ns = apply_osae(shape, plan, legs, st, po, pr)
    if len(nl) == len(legs):
        assert ns == st, "押さえなかったのに賭け金が変わっている"
    else:
        assert mean_expected_payout(ns, po) > MIN_MEAN_PAYOUT
