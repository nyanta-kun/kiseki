"""型F の入稿ゲート・フォールバックを固定する（2026-09-03）。

`F_hit`（帯なし12点）は平均想定払戻 2万円のゲートに**24%落ちる**。落ちた分だけ
帯15倍で組み直すと、既存の買い目を1つも書き換えないまま在庫が +2.2件/日 増え、
2倍以上の的中が +4.7〜6.5% 増える（表示的中の代償はほぼゼロ・両窓一致）。

🔴 ここで固定するのは**発火条件**。「落ちたときだけ」という条件が壊れると
   置換になり、測って不採用にした案（表示的中 −0.8pt）へ静かに変わる。
"""
from __future__ import annotations

import itertools

import pytest

from src.stake_allocation import MIN_MEAN_PAYOUT
from src.type_lab import (
    GATE_FALLBACK, PLANS, RaceShape, build_with_gate_fallback,
    mean_expected_payout, rule_version,
)

PERMS = list(itertools.permutations(range(1, 8), 3))


def _shape() -> RaceShape:
    """型F（大混戦）の盤面。`build_legs` は order しか見ない。"""
    return RaceShape("F", 1.10, 3, 0.10, False, tuple(range(1, 8)), 1.9)


def _flat_board(odds: float) -> tuple[dict, dict]:
    """全210点が同じ予測オッズ・同じ確率の盤面。"""
    po = {c: odds for c in PERMS}
    pr = {c: 1.0 / len(PERMS) for c in PERMS}
    return po, pr


def test_fallback_is_defined_for_the_two_measured_plans_only():
    """フォールバックを持つのは実測した2つだけ（増やすときは実測を伴うこと）。

    `F_hit` … 帯なし12点がゲートに落ちた分を「帯15倍＋帯下1点差込」→「帯15倍」で拾う
    `C_hit` … 帯下の1点を差し込んだ結果落ちた分を、差込なしの12点で拾う（2026-09-08）
    """
    assert set(GATE_FALLBACK) == {"C_hit", "F_hit"}
    for k, vs in GATE_FALLBACK.items():
        assert isinstance(vs, tuple) and vs, f"{k} の値は試す順の並びであること"
    f0, f1 = GATE_FALLBACK["F_hit"]
    assert f0.min_odds == f1.min_odds == 15.0
    assert f0.underband_min == 5.0, "1段目は帯下の1点を差し込む"
    assert f1.underband_min == 0.0, "2段目は差込なし（在庫を落とさないための受け皿）"
    for f in (f0, f1):
        assert f.max_legs == PLANS["F_hit"].max_legs
        assert f.alloc == PLANS["F_hit"].alloc
    # C_hit の代替は「差込を外しただけ」＝他の属性は本命と同じであること
    (c0,) = GATE_FALLBACK["C_hit"]
    assert c0.underband_min == 0.0
    for f in ("min_odds", "max_legs", "alloc", "floor_mult", "structure"):
        assert getattr(c0, f) == getattr(PLANS["C_hit"], f), f


def test_fallback_keeps_the_same_plan_key():
    """🔴 代替は元と同じ `key` を名乗る。別名だと1レース2商品になる。"""
    for key, vs in GATE_FALLBACK.items():
        for v in vs:
            assert v.key == key


def test_no_fallback_when_the_gate_already_passes():
    """本命がゲートを通るなら、代替へ切り替えない（既存の買い目を守る）。"""
    po, pr = _flat_board(60.0)          # 12点均等なら想定払戻 ≈ 50,000円
    legs, stakes, used = build_with_gate_fallback(_shape(), PLANS["F_hit"], po, pr)
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT
    assert used is PLANS["F_hit"]
    assert used.min_odds == 5.0          # 帯15倍は掛かっていない（本命の下限だけ）


def test_fallback_fires_when_the_gate_would_reject():
    """本命がゲートに落ちるレースでは、帯つきの代替で組み直す。

    ⚠️ 盤面は**信頼度傾斜が組める条件**を満たすこと。床は 予算×`DEFAULT_FLOOR_MULT`
       ÷ オッズ なので Σ(1/オッズ) <= 1/1.3 = 0.769 が要る。満たさないと
       `allocate` が None を返し、ゲート以前に「組めないだけ」の検査になる。
       ＝ 12点すべてを15倍未満にはできない（平均15.6倍以上が構造的に要る）ので、
       **安い点を混ぜて**平均想定払戻を押し下げる盤面を作る。

    実測（この盤面）: F_hit 17,333円 → ゲート落ち / 代替 33,333円 → 通る
    """
    shape = _shape()
    cheap, rich = PERMS[:2], PERMS[2:]
    po = {c: 8.0 for c in cheap} | {c: 40.0 for c in rich}
    w = {c: 100.0 for c in cheap} | {c: 1.0 for c in rich}
    tot = sum(w.values())
    pr = {c: v / tot for c, v in w.items()}

    base = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr,
                                    min_mean_payout=10 ** 12)   # 必ず落とす
    assert base is not None and base[2] is PLANS["F_hit"]
    assert set(cheap) <= set(base[0]), "前提が崩れている: F_hit は安い点を採るはず"
    assert mean_expected_payout(base[1], po) <= MIN_MEAN_PAYOUT, (
        "前提が崩れている: この盤面では F_hit がゲートに落ちるはず")

    legs, stakes, used = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr)
    assert used in GATE_FALLBACK["F_hit"]
    assert used.key == "F_hit", "代替が別の plan_key を名乗ると1レース2商品になる"
    # 🔴 1段目は帯下の最人気を1点だけ差し込むので「全点が帯以上」ではない。
    #    例外はその1点だけで、`underband_min` は下回らないこと。
    under = [c for c in stakes if po[c] < 15.0]
    assert len(under) <= 1, "帯15倍未満の点を2点以上買っている"
    assert all(po[c] >= used.underband_min for c in under)
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT


def test_plans_without_a_fallback_are_untouched():
    """フォールバックを持たないプランは、ゲートに落ちてもそのまま返す
    （見送りの判断は入稿側の責務）。"""
    po, pr = _flat_board(2.5)           # 想定払戻が2万円に届かない
    got = build_with_gate_fallback(_shape(), PLANS["E_hit"], po, pr)
    if got is not None:                 # 組めるなら本命のまま
        assert got[2] is PLANS["E_hit"]


def test_rule_version_splits_on_fallback_change(monkeypatch):
    """🔴 帯を動かしたら版が割れること。割れないと新旧の行が同じ版で混ざる。"""
    from dataclasses import replace
    before = rule_version(7)
    alt = dict(GATE_FALLBACK)
    alt["F_hit"] = tuple(replace(v, min_odds=20.0) for v in GATE_FALLBACK["F_hit"])
    monkeypatch.setattr("src.type_lab.GATE_FALLBACK", alt)
    assert rule_version(7) != before


def test_rule_version_splits_when_the_fallback_order_changes(monkeypatch):
    """🔴 試す順を入れ替えたら版が割れること（順序で商品が変わるため）。"""
    before = rule_version(7)
    alt = dict(GATE_FALLBACK)
    alt["F_hit"] = tuple(reversed(GATE_FALLBACK["F_hit"]))
    monkeypatch.setattr("src.type_lab.GATE_FALLBACK", alt)
    assert rule_version(7) != before


def test_generator_goes_through_the_fallback():
    """🔴 生成側が `build_legs` を直に呼んでいないこと（paper と live の母集団が
    割れる事故を静的に止める）。"""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_type_lab_picks.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "rows_for_race")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "build_with_gate_fallback" in called
    assert "build_legs" not in called, "rows_for_race が build_legs を直接呼んでいる"


def test_fallback_is_seven_car_only():
    """🔴 9車には掛けない（測ったのは7車だけ・オッズ分布が丸ごと違う）。"""
    shape = _shape()
    cheap, rich = PERMS[:2], PERMS[2:]
    po = {c: 8.0 for c in cheap} | {c: 40.0 for c in rich}
    w = {c: 100.0 for c in cheap} | {c: 1.0 for c in rich}
    tot = sum(w.values())
    pr = {c: v / tot for c, v in w.items()}

    _, _, used7 = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, 7)
    _, _, used9 = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, 9)
    assert used7 in GATE_FALLBACK["F_hit"]
    assert used9 is PLANS["F_hit"], "9車でフォールバックが発火している"


def test_nine_car_rule_version_is_unchanged_by_the_fallback(monkeypatch):
    """🔴 9車の挙動は変えていないので、9車の版は割らないこと。"""
    from dataclasses import replace
    before9 = rule_version(9)
    alt = dict(GATE_FALLBACK)
    alt["F_hit"] = tuple(replace(v, min_odds=20.0) for v in GATE_FALLBACK["F_hit"])
    monkeypatch.setattr("src.type_lab.GATE_FALLBACK", alt)
    assert rule_version(9) == before9
