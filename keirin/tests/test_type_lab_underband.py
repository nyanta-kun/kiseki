"""型C の「帯の下から最人気の1点を買い足す」を固定する（2026-09-08）。

発端は 2026-09-08 大垣7R。決着 7-3-5 は p3 上位3車の順当決着かつ**市場最人気**
（6.0倍）だったのに、`min_odds=15.0` の帯に切られて買い目に無かった。
C_hit が外したレースの 29.7 / 26.9%（探索 / 確認）がこの形。

固定するのは次の4点。どれが壊れても**エラーは出ず、静かに別の商品になる**:

1. 差し込むのは **1点だけ**（2点にすると +6.47/+5.28pt → +3.81/+2.93pt へ落ちる）
2. 選ぶのは **最も人気（予測オッズ最小）の目**であって、モデル確率1位ではない
   （対照実験でモデル1位は無作為と区別できなかった。`_insert_underband` の docstring）
3. **点数は12点のまま**（確率最下位の1点と入れ替える）
4. **先頭へ入れる**（`line_legs` が後ろから置き換えるので、末尾だと真っ先に消える）

🔴 **2026-09-22: 差込を持つのは `GATE_FALLBACK["F_hit"][0]` だけになった。**
`C_hit` が計画払戻5万円のダッチ（帯なし）へ移ったため（`type_lab.HIT_BAND_TARGET`）。
仕組み自体は型F のゲート落ちの受け皿で生きているので、検査もそちらへ移す。

実測と再現: `docs/type_lab/type_c.md` 11章 /
`scripts/exp_type_lab/type_c_lowband.py`（diag / arms / ctrl / band / final / verify）
"""
from __future__ import annotations

import itertools

from src.stake_allocation import MIN_MEAN_PAYOUT
from src.type_lab import (
    GATE_FALLBACK, PLANS, RaceShape, build_legs, build_with_gate_fallback,
    mean_expected_payout, rule_version,
)

PERMS = list(itertools.permutations(range(1, 8), 3))
#: 大垣7R の p3 順（7-3-5-6-2-1-4）。`build_legs` は order しか見ない。
ORDER = (7, 3, 5, 6, 2, 1, 4)


#: 差込を持つ唯一のプラン（型F がゲートに落ちたときの1段目）。
UNDER = GATE_FALLBACK["F_hit"][0]


def _shape() -> RaceShape:
    return RaceShape("F", 1.1728, 2, 0.20, False, ORDER, 1.5)


def _board(cheap: dict[tuple, float]) -> tuple[dict, dict]:
    """`cheap` の目だけ安く、残りは 40倍の板。確率は 1/オッズ に比例。"""
    po = {c: 40.0 for c in PERMS} | cheap
    tot = sum(1.0 / v for v in po.values())
    return po, {c: (1.0 / v) / tot for c, v in po.items()}


# ───────────────────────── 差し込みそのもの ─────────────────────────

def test_the_cheapest_under_band_point_is_bought():
    """帯15倍未満の最人気の目が買い目に入る（＝大垣7R の 7-3-5 を拾う）。"""
    po, pr = _board({(7, 3, 5): 6.2, (7, 5, 3): 6.9, (3, 7, 5): 14.0})
    legs = build_legs(_shape(), UNDER, po, pr)
    assert legs is not None
    assert legs[0] == (7, 3, 5), "帯下の最人気を先頭に入れていない"
    assert len(legs) == 12, "点数が12点から動いている"


def test_only_one_under_band_point_is_inserted():
    """🔴 差し込むのは1点だけ。2点目は +2.9〜3.8pt まで効果が落ちる。"""
    po, pr = _board({(7, 3, 5): 6.2, (7, 5, 3): 6.9, (3, 7, 5): 14.0})
    legs = build_legs(_shape(), UNDER, po, pr)
    under = [c for c in legs if po[c] < UNDER.min_odds]
    assert under == [(7, 3, 5)]


def test_the_pick_is_by_market_price_not_by_model_probability():
    """🔴 選ぶのは**最も人気の目**。モデル確率1位を選ぶ実装にしてはいけない
    （対照でモデル1位は無作為と区別できず、市場1位が最良だった）。"""
    po = {c: 40.0 for c in PERMS} | {(7, 3, 5): 6.2, (3, 7, 5): 9.0}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / v) / tot for c, v in po.items()}
    pr[(3, 7, 5)] = 1.0                       # 確率だけ 3-7-5 を最上位にする
    legs = build_legs(_shape(), UNDER, po, pr)
    assert legs[0] == (7, 3, 5), "確率で選んでいる（市場最人気で選ぶこと）"


def test_no_insert_when_nothing_sits_under_the_band():
    """帯下に目が無ければ何もしない（現行と同じ12点）。"""
    po, pr = _board({})
    legs = build_legs(_shape(), UNDER, po, pr)
    assert all(po[c] >= UNDER.min_odds for c in legs)
    assert len(legs) == 12


def test_points_below_the_floor_are_not_bought():
    """`underband_min` 未満（＝人気すぎる目）は拾わない。"""
    po, pr = _board({(7, 3, 5): 2.5})         # 5.0倍未満
    legs = build_legs(_shape(), UNDER, po, pr)
    assert (7, 3, 5) not in legs


def test_other_plans_are_untouched():
    """🔴 差込を持つのは型F のゲート落ちの1段目だけ。売り物の本命に漏れていないこと。"""
    assert UNDER.underband_min == 5.0
    for key, plan in PLANS.items():
        assert plan.underband_min == 0.0, f"{key} に差込が漏れている"
    assert GATE_FALLBACK["F_hit"][1].underband_min == 0.0, "2段目は差込なしの受け皿"
    assert GATE_FALLBACK["C_hit"][0].underband_min == 0.0


# ───────────────────────── ゲートとの噛み合わせ ─────────────────────────

def test_falls_back_to_the_plain_twelve_when_the_gate_would_reject():
    """🔴 **差込でゲートを割っても在庫を落とさない**（母集団を静かに削らない）。

    ── 2026-09-22: 対象を型F の連鎖へ移した（型C は帯を持たなくなった）。
    型F は「帯なし12点」→「帯15倍＋差込」→「帯15倍」の3段で、どこで通っても
    `plan_key` は `F_hit` のまま。守るのは在庫が消えないことと、差込が割った分を
    差込なしの段が受けることの2つ。
    """
    shape = _shape()
    # 残りが 28倍の板。差し込むと安い点の床（予算×2.0÷6.2 ≈ 3,300円）が予算の
    # 3割を食う。差込なしなら通る。
    po = {c: 28.0 for c in PERMS} | {(7, 3, 5): 6.2}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / v) / tot for c, v in po.items()}

    legs, stakes, used = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr,
                                                  n_entries=7)
    assert stakes, "ゲートに落ちたのに在庫を落としている"
    assert used.key == "F_hit", "代替が別の plan_key を名乗ると1レース2商品になる"
    assert used is PLANS["F_hit"] or used in GATE_FALLBACK["F_hit"]
    # 差込の段で通ったなら、その1点が買い目に残っていること
    if used.underband_min:
        assert (7, 3, 5) in stakes


def test_no_fallback_when_the_insert_already_passes_the_gate():
    """差込のままゲートを通るなら、代替へ切り替えない。"""
    po, pr = _board({(7, 3, 5): 12.0})
    legs, stakes, used = build_with_gate_fallback(
        _shape(), UNDER, po, pr, n_entries=7)
    assert used is UNDER
    assert (7, 3, 5) in stakes
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT


def test_insert_is_seven_car_only():
    """🔴 9車では差し込まない。測ったのは7車だけで、9車は `GATE_FALLBACK` も
    掛からない（ゲートに落ちたら在庫がそのまま消える）。"""
    po, pr = _board({(7, 3, 5): 6.2})
    _, st7, used7 = build_with_gate_fallback(_shape(), UNDER, po, pr, 7)
    _, st9, used9 = build_with_gate_fallback(_shape(), UNDER, po, pr, 9)
    assert used7.underband_min == 5.0 and (7, 3, 5) in st7
    assert used9.underband_min == 0.0 and (7, 3, 5) not in st9


# ───────────────────────── 版 ─────────────────────────

def test_rule_version_splits_when_the_floor_moves(monkeypatch):
    """🔴 下限を動かしたら7車の版が割れること（割れないと新旧の行が混ざる）。"""
    from dataclasses import replace
    before = rule_version(7)
    alt = dict(GATE_FALLBACK) | {
        "F_hit": (replace(UNDER, underband_min=8.0), GATE_FALLBACK["F_hit"][1])}
    monkeypatch.setattr("src.type_lab.GATE_FALLBACK", alt)
    assert rule_version(7) != before


def test_nine_car_rule_version_is_unchanged(monkeypatch):
    """🔴 9車の挙動は変えていないので、9車の版は割らないこと。"""
    from dataclasses import replace
    before9 = rule_version(9)
    alt = dict(GATE_FALLBACK) | {
        "F_hit": (replace(UNDER, underband_min=8.0), GATE_FALLBACK["F_hit"][1])}
    monkeypatch.setattr("src.type_lab.GATE_FALLBACK", alt)
    assert rule_version(9) == before9


# ───────────────────────── 文面 ─────────────────────────

def test_submission_band_table_matches_the_plans():
    """🔴 文面モジュールの帯の写しが `PLANS` とずれていないこと。

    `type_lab_submission.py` は標準ライブラリしか import できない（backend が
    ファイル読み込みで束縛する）ので帯を写している。ずれると**文面だけが**
    静かに事実と食い違う（買っていない1点を説明する／説明しない）。
    """
    from src.type_lab_submission import UNDERBAND_BANDS
    assert UNDERBAND_BANDS == {k: p.min_odds for k, p in PLANS.items()
                               if p.underband_min}
    # 🔴 2026-09-22: 差込は `GATE_FALLBACK["F_hit"]` にしか無く、そちらは
    #    「5〜15倍が通常の買い目か差込か見分けられない」ため意図して載せていない。
    assert UNDERBAND_BANDS == {}


def test_the_copy_no_longer_claims_we_avoid_the_obvious():
    """🔴 「素直な決着は買わない」は 2026-09-08 から嘘なので書かないこと。"""
    from src.type_lab_submission import PLAN_BODIES, TYPE_NOTES
    assert "素直な決着は買わない" not in PLAN_BODIES["C_hit"]
    # 🔴 2026-09-22: 型C は帯そのものを持たなくなったので、見解の側の
    #    「配当の付く帯へ寄せています」も消してある（買い方を見解に書かない）。
    assert "帯" not in TYPE_NOTES["C"]


def test_the_copy_is_written_only_when_the_point_is_actually_bought():
    """🔴 差込の一文は**買い目から導く**。差し込めなかったレースで固定文を出すと、
    買っていないものを説明することになる。

    ── 2026-09-22: `UNDERBAND_BANDS` が空になったので、**いまはどのプランにも
    付かない**のが正しい状態。仕組みが生きていること（表に載せれば買い目から
    判定して付くこと）を併せて固定する。
    """
    import src.type_lab_submission as SUB
    from src.type_lab_submission import build_comment, underband_note

    with_ = [{"combo": "7-3-5", "stake": 3400, "pred_odds": 6.2},
             {"combo": "7-3-2", "stake": 1300, "pred_odds": 15.8}]
    without = [{"combo": "7-3-2", "stake": 1300, "pred_odds": 15.8},
               {"combo": "5-7-3", "stake": 1000, "pred_odds": 20.3}]

    for key in PLANS:
        assert underband_note(key, with_) == "", key

    orig = SUB.UNDERBAND_BANDS
    try:
        SUB.UNDERBAND_BANDS = {"C_hit": 15.0}
        assert underband_note("C_hit", with_)
        assert underband_note("C_hit", without) == ""
        body = build_comment("C_hit", "C", 7, 3, with_, "trifecta")
        assert "人気を集めている目も1点だけ押さえています" in body
        assert "人気を集めている" not in build_comment(
            "C_hit", "C", 7, 3, without, "trifecta")
    finally:
        SUB.UNDERBAND_BANDS = orig
