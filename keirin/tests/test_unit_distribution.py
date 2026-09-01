"""賭け金配分の共通核と、3方針の振る舞いが変わっていないことを固定する。

## 背景（2026-09-01）

「予算を重みに比例して口数で配る」処理が3箇所に独立実装されていた。
無作為3,000件で突き合わせたところ **3つが一致するのは 5.1% だけ**
（A↔B 75.0% / A↔C 93.4% / B↔C 85.2% が不一致）。つまり重複ではなく
**契約の違う3つの配分方針**だった。

だから丸ごと1つにはせず、配り方の骨格だけを `src/unit_distribution.py` に
寄せ、方針の違いは引数で明示した。

## このテストが守るもの

1. **リファクタで金額が1円も動いていないこと**
   `tests/fixtures/stake_allocation_golden.json` は**共通化する前**の
   3実装の出力 400 件。ここが1件でもズレたら入稿金額が変わったということ。
   🔴 入稿・採点経路は壊れても例外が出ない箇所が多いので、
      「動いた＝正しい」では確認にならない。金額そのものを突き合わせる。

2. **3方針が実際に違うものであり続けること**
   将来「同じに見えるから」と1つに畳まれると、旧ランクの入稿金額か
   型ラボの買い目集合のどちらかが黙って変わる。違いを明示的に固定する。

3. **核そのものの不変条件**（合計・上限・最低1口）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.dutch_allocation import _round_stakes
from src.stake_allocation import allocate_budget
from src.type_lab import Plan, allocate
from src.unit_distribution import (
    FLOOR_THEN_LARGEST_REMAINDER,
    FLOOR_THEN_LOWEST_PAYOUT,
    RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT,
    distribute_units,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "stake_allocation_golden.json").read_text(encoding="utf-8")
)
BUDGET, UNIT, CAP = GOLDEN["budget"], GOLDEN["unit"], GOLDEN["cap"]

_PLAN_DUTCH = Plan(key="x", type_label="x", bet_type="trifecta", structure="x",
                   n_partners=0, alloc="dutch")
_PLAN_CONF = Plan(key="y", type_label="y", bet_type="trifecta", structure="y",
                  n_partners=0, alloc="conf")


def _cases():
    for i, c in enumerate(GOLDEN["cases"]):
        yield i, {int(k): v for k, v in c["odds"].items()}, {int(k): v for k, v in c["probs"].items()}, c


class Test振る舞いが変わっていない:
    """共通化の前後で出力が1件も変わらないこと（golden 400 件）。"""

    def test_旧ランク入稿の配分(self) -> None:
        bad = []
        for i, odds, _probs, c in _cases():
            got = _round_stakes(odds, list(odds), BUDGET, UNIT, CAP)
            exp = {int(k): v for k, v in c["round_stakes"].items()}
            if got != exp:
                bad.append((i, got, exp))
        assert not bad, f"_round_stakes が {len(bad)} 件変わりました。先頭: {bad[0]}"

    def test_相手別配分(self) -> None:
        bad = []
        for i, odds, _probs, c in _cases():
            exp_raw = c["allocate_budget"]
            if "__error__" in exp_raw:
                continue
            got = allocate_budget({k: 1.0 / odds[k] for k in odds}, BUDGET, UNIT)
            exp = {int(k): v for k, v in exp_raw.items()}
            if got != exp:
                bad.append((i, got, exp))
        assert not bad, f"allocate_budget が {len(bad)} 件変わりました。先頭: {bad[0]}"

    @pytest.mark.parametrize(("name", "plan"),
                             [("type_lab_dutch", _PLAN_DUTCH), ("type_lab_conf", _PLAN_CONF)])
    def test_型ラボの配分(self, name: str, plan: Plan) -> None:
        bad = []
        for i, odds, probs, c in _cases():
            got = allocate(list(odds), odds, probs, plan, BUDGET, UNIT)
            exp = c[name]
            exp = None if exp is None else {int(k): v for k, v in exp.items()}
            if got != exp:
                bad.append((i, got, exp))
        assert not bad, f"type_lab.allocate({plan.alloc}) が {len(bad)} 件変わりました。先頭: {bad[0]}"


class Test3方針は別物:
    """「同じに見えるから」と畳まれるのを防ぐ。"""

    def test_端数の寄せ先が方針で変わる(self) -> None:
        w = {0: 1 / 14.9, 1: 1 / 18.9, 2: 1 / 24.2, 3: 1 / 8.3}
        lr = distribute_units(w, 100, leftover=FLOOR_THEN_LARGEST_REMAINDER)
        ro = distribute_units(w, 100, leftover=RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT)
        assert lr != ro, "実測で 85.2% 食い違う2方針が一致しています"

    def test_最低1口保証は方針で変わる(self) -> None:
        """人気薄を含む配分では、保証なしだと 0 口の点が出る。"""
        w = {0: 1 / 2.0, 1: 1 / 400.0}
        assert distribute_units(w, 100, leftover=FLOOR_THEN_LARGEST_REMAINDER)[1] == 0
        assert distribute_units(w, 100, leftover=RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT)[1] >= 1

    def test_オッズを見る方針と見ない方針がある(self) -> None:
        """🔴 LOWEST_PAYOUT だけがオッズを参照する。確定オッズを渡すと先読みになる。"""
        w = {0: 1 / 10.0, 1: 1 / 10.0, 2: 1 / 10.0}
        with pytest.raises(ValueError):
            distribute_units(w, 100, leftover=FLOOR_THEN_LOWEST_PAYOUT)
        # 見ない方針はオッズ無しで成立する
        assert sum(distribute_units(w, 100, leftover=FLOOR_THEN_LARGEST_REMAINDER).values()) == 100


class Test核の不変条件:
    def test_合計はちょうど配り切る(self) -> None:
        w = {0: 0.5, 1: 0.3, 2: 0.2}
        for lo in (FLOOR_THEN_LARGEST_REMAINDER, RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT):
            assert sum(distribute_units(w, 100, leftover=lo).values()) == 100

    def test_上限を超えない(self) -> None:
        w = {0: 0.9, 1: 0.1}
        u = distribute_units(w, 100, leftover=FLOOR_THEN_LARGEST_REMAINDER, cap_units=50)
        assert max(u.values()) <= 50

    def test_口数が足りなければ失敗する(self) -> None:
        """最低1口保証の方針は、配れないなら黙って減らさず落とす。"""
        w = {i: 1.0 for i in range(5)}
        with pytest.raises(ValueError):
            distribute_units(w, 3, leftover=RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT)

    def test_重みが空や0なら失敗する(self) -> None:
        with pytest.raises(ValueError):
            distribute_units({}, 10)
        with pytest.raises(ValueError):
            distribute_units({0: 0.0, 1: 0.0}, 10)

    def test_未知の方針は失敗する(self) -> None:
        with pytest.raises(ValueError):
            distribute_units({0: 1.0}, 10, leftover="なにか")
