"""段（固め・広め・手広く）の入稿ゲートを「当たったときの払戻の悪い側」で判定する（2026-09-15）。

ユーザー決定: 各点の **賭け金 × 予測オッズ × 下振れ係数** の最小 < 1.5万円なら見送る。
下振れ係数は当たり目の 確定÷予測 の p25（`type_lab.TIER_REALIZED_FACTOR`）。
対象は T_firm / T_mid / T_axis だけ（他プラン・9車は変えない）。

ここで固定するのは:
  1. 係数表は n >= 30 のセルだけ・参照順 (帯,種別,段)→(帯,*,段)→(帯,種別,*)→(帯,*,*)
  2. 帯・段・種別の境目
  3. 判定できない点は飛ばし、全部判定できなければ通す
  4. ゲートは新判定で落ちる／通る。他プランは変わらない
  5. 係数表を動かすと 7車の `rule_version` だけが割れる
"""
from __future__ import annotations

import pytest

import src.type_lab as T
from src.type_lab import (
    TIER_AXIS_FIRM_MIN, TIER_AXIS_MID_MIN, TIER_POINT_GATE_PLANS,
    TIER_POINT_PAYOUT_MIN, TIER_REALIZED_BANDS, TIER_REALIZED_FACTOR,
    TIER_REALIZED_MIN_N, TIER_REALIZED_QUANTILE, UPPER_BAND_PLANS,
    tier_realized_factor, tier_realized_min_payout,
)


def _f(key):
    return TIER_REALIZED_FACTOR[key][1]


# ───────────────────────── 係数表 ─────────────────────────

def test_ユーザー決定の定数():
    assert TIER_POINT_PAYOUT_MIN == 15_000
    assert TIER_REALIZED_QUANTILE == 0.25
    assert TIER_REALIZED_MIN_N == 30
    assert TIER_REALIZED_BANDS == (5.0, 10.0, 20.0, 40.0)
    assert TIER_POINT_GATE_PLANS == frozenset({"T_firm", "T_mid", "T_axis"})


def test_係数表はn30以上のセルだけ():
    assert TIER_REALIZED_FACTOR
    for (b, ch, t), (n, v) in TIER_REALIZED_FACTOR.items():
        assert n >= TIER_REALIZED_MIN_N, (b, ch, t, n)
        assert b in range(len(TIER_REALIZED_BANDS) + 1)
        assert ch in ("challenge", "other", "*")
        assert t in ("firm", "mid", "upset", "*")
        # 🔴 係数は全セル 1 未満（新判定が旧判定「予測のまま >= 1.5万」を含む前提）
        assert 0 < v < 1, (b, ch, t, v)


@pytest.mark.parametrize("b", range(len(TIER_REALIZED_BANDS) + 1))
def test_全帯に最後の参照先がある(b):
    """(帯,*,*) が無いと、その帯の目は判定できずに素通しになる。"""
    assert (b, "*", "*") in TIER_REALIZED_FACTOR


def test_参照順_最も細かいセルを先に使う():
    assert tier_realized_factor(12.0, "予選", 1.50) == _f((2, "other", "firm"))
    assert tier_realized_factor(12.0, "チャレンジ予選", 1.50) == _f((2, "challenge", "firm"))


def test_参照順_細かいセルがn30未満なら種別を外す():
    """(1,チャレンジ,広め) は n=27 で表に無い → (1,*,広め)。"""
    assert (1, "challenge", "mid") not in TIER_REALIZED_FACTOR
    assert tier_realized_factor(6.0, "チャレンジ予選", 1.40) == _f((1, "*", "mid"))


def test_参照順_段つきが両方無ければ段を外す():
    """(0,チャレンジ,荒れ) n=5・(0,*,荒れ) n=20 → (0,チャレンジ,*)。"""
    assert (0, "challenge", "upset") not in TIER_REALIZED_FACTOR
    assert (0, "*", "upset") not in TIER_REALIZED_FACTOR
    assert tier_realized_factor(3.0, "チャレンジ選抜", 1.30) == _f((0, "challenge", "*"))
    # 他の種別も同じ（(0,他,荒れ) n=15）
    assert tier_realized_factor(3.0, "予選", 1.30) == _f((0, "other", "*"))


def test_参照順_最後は帯だけ(monkeypatch):
    table = {(0, "*", "*"): (100, 0.5)}
    monkeypatch.setattr(T, "TIER_REALIZED_FACTOR", table)
    assert T.tier_realized_factor(3.0, "チャレンジ", 1.50) == 0.5
    # n<30 のセルが紛れても使わない
    monkeypatch.setattr(T, "TIER_REALIZED_FACTOR", {(0, "*", "*"): (29, 0.5)})
    assert T.tier_realized_factor(3.0, "予選", 1.50) is None


def test_帯の境目は左閉じ():
    assert tier_realized_factor(4.99, "予選", 1.50) == _f((0, "other", "firm"))
    assert tier_realized_factor(5.0, "予選", 1.50) == _f((1, "other", "firm"))
    assert tier_realized_factor(39.99, "予選", 1.50) == _f((3, "other", "firm"))
    assert tier_realized_factor(40.0, "予選", 1.50) == _f((4, "other", "firm"))
    assert tier_realized_factor(900.0, "予選", 1.50) == _f((4, "other", "firm"))


def test_段の境目は段商品と同じ():
    """境目ちょうどは下の段（`tier_plan_key` と同じ `>`）。"""
    assert tier_realized_factor(12.0, "予選", TIER_AXIS_FIRM_MIN) == _f((2, "other", "mid"))
    assert tier_realized_factor(12.0, "予選", TIER_AXIS_FIRM_MIN + 1e-4) == _f((2, "other", "firm"))
    assert tier_realized_factor(12.0, "予選", TIER_AXIS_MID_MIN) == _f((2, "other", "upset"))
    assert tier_realized_factor(12.0, "予選", TIER_AXIS_MID_MIN + 1e-4) == _f((2, "other", "mid"))


def test_種別はチャレンジで始まるものだけ():
    assert tier_realized_factor(12.0, "チャレンジ決勝", 1.50) == _f((2, "challenge", "firm"))
    assert tier_realized_factor(12.0, "ガールズチャレンジ", 1.50) == _f((2, "other", "firm"))
    assert tier_realized_factor(12.0, None, 1.50) == _f((2, "other", "firm"))


def test_軸信頼が無ければ段を外して引く():
    assert tier_realized_factor(12.0, "予選", None) == _f((2, "other", "*"))


@pytest.mark.parametrize("odds", [None, 0, -1.0, "x"])
def test_予測オッズが無ければ判定できない(odds):
    assert tier_realized_factor(odds, "予選", 1.50) is None


# ───────────────────────── 悪い側の最小払戻 ─────────────────────────

def test_最小払戻は各点の賭け金x予測x係数の最小():
    legs = [{"combo": "1-2-3", "stake": 5000, "pred_odds": 4.0},     # 帯0
            {"combo": "1-3-2", "stake": 2000, "pred_odds": 12.0}]    # 帯2
    got = tier_realized_min_payout(legs, "予選", 1.50)
    want = min(5000 * 4.0 * _f((0, "other", "firm")), 2000 * 12.0 * _f((2, "other", "firm")))
    assert got == pytest.approx(want)


def test_判定できない点は飛ばし全部できなければNone():
    legs = [{"combo": "1-2-3", "stake": 0, "pred_odds": 3.0},
            {"combo": "1-3-2", "stake": 3000},
            {"combo": "2-1-3", "stake": 5000, "pred_odds": 4.0}]
    assert tier_realized_min_payout(legs, "予選", 1.50) == pytest.approx(
        5000 * 4.0 * _f((0, "other", "firm")))
    assert tier_realized_min_payout(legs[:2], "予選", 1.50) is None
    assert tier_realized_min_payout([], "予選", 1.50) is None


def test_段のプランには上帯を重ねない():
    """🔴 ゲートは `legs` 全点で見る。段に上帯（高オッズの押さえ）が重なると、
    本線ではない目で落ちる。重ねるならゲートの入力を本線の点に絞ること。"""
    assert not (TIER_POINT_GATE_PLANS & UPPER_BAND_PLANS)


# ───────────────────────── 入稿ゲート ─────────────────────────

def _row(plan_key, stake, odds, race_type="予選", axis_sum=1.50, **kw):
    legs = [{"combo": "1-2-3", "stake": stake, "pred_odds": odds}]
    return dict(plan_key=plan_key, race_type=race_type, axis_sum=axis_sum,
                pred_mean_payout=stake * odds, pred_min_payout=stake * odds,
                legs=legs, **kw)


@pytest.mark.parametrize("plan_key", sorted(TIER_POINT_GATE_PLANS))
def test_ゲートは悪い側の見込みで落とす(plan_key):
    import scripts.netkeirin_submit_type_lab as m
    f = _f((0, "other", "firm"))
    # 予測のままなら 17,500円（旧ゲートは通す）が、悪い側は 13,462円 → 落とす
    low = _row(plan_key, 5000, 3.5)
    assert 5000 * 3.5 >= TIER_POINT_PAYOUT_MIN
    got = m._gate_reason(low)
    assert got is not None and got[0] == m.SKIP_GATE_MEAN_PAYOUT
    assert "当たったとき" in got[1] and f"{5000 * 3.5 * f:,.0f}円" in got[1]
    # 悪い側 15,385円 → 通す
    assert m._gate_reason(_row(plan_key, 5000, 4.0)) is None


def test_ゲートの境目は15000円ちょうどで通す(monkeypatch):
    import scripts.netkeirin_submit_type_lab as m
    monkeypatch.setattr(m, "tier_realized_min_payout", lambda *a, **k: 15_000.0)
    assert m._gate_reason(_row("T_firm", 5000, 4.0)) is None
    monkeypatch.setattr(m, "tier_realized_min_payout", lambda *a, **k: 14_999.99)
    assert m._gate_reason(_row("T_firm", 5000, 4.0)) is not None


def test_ゲートは種別と軸信頼で係数を変える():
    import scripts.netkeirin_submit_type_lab as m
    # 帯4: チャレンジ×固め 0.549 ↔ 他×固め 0.678。予測 25,000円
    assert m._gate_reason(_row("T_firm", 500, 50.0, race_type="予選")) is None
    assert m._gate_reason(_row("T_firm", 500, 50.0, race_type="チャレンジ予選")) is not None


def test_悪い側を判定できなければ見送らない():
    import scripts.netkeirin_submit_type_lab as m
    row = dict(plan_key="T_mid", race_type="予選", axis_sum=1.40,
               pred_min_payout=1000, legs=[{"combo": "1-2-3", "stake": 5000}])
    assert m._gate_reason(row) is None


def test_2倍未満の目は従来どおり落とす():
    import scripts.netkeirin_submit_type_lab as m
    got = m._gate_reason(_row("T_firm", 12_000, 1.9))
    assert got is not None and got[0] == m.SKIP_GATE_POINT_ODDS


@pytest.mark.parametrize("plan_key", ["T_upset", "C_hit", "A_ana", "F_line"])
def test_段の点ゲート以外は変わらない(plan_key):
    """平均想定払戻 > 2万円 のまま。悪い側の見込みでは判定しない。"""
    import scripts.netkeirin_submit_type_lab as m
    # 平均 25,000円（悪い側なら 1.5万円を割る）→ 通す
    assert m._gate_reason(_row(plan_key, 500, 50.0, race_type="チャレンジ予選")) is None
    # 平均 19,000円 → 落とす
    assert m._gate_reason(_row(plan_key, 500, 38.0)) is not None


# ───────────────────────── 版 ─────────────────────────

def test_係数表を動かすと7車の版だけ割れる(monkeypatch):
    v7, v9 = T.rule_version(7), T.rule_version(9)
    table = dict(TIER_REALIZED_FACTOR)
    k = next(iter(table))
    table[k] = (table[k][0], table[k][1] + 0.01)
    monkeypatch.setattr(T, "TIER_REALIZED_FACTOR", table)
    assert T.rule_version(7) != v7
    assert T.rule_version(9) == v9
    monkeypatch.setattr(T, "TIER_REALIZED_FACTOR", TIER_REALIZED_FACTOR)
    monkeypatch.setattr(T, "TIER_REALIZED_QUANTILE", 0.3)
    assert T.rule_version(7) != v7
