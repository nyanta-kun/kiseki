"""賭け金配分の共通核 — 「重みに比例して口数を配る」の一箇所。

## なぜ切り出すか（2026-09-01）

同じ「予算を 1/オッズ に比例して口数で配る」処理が3箇所に独立実装されていた:

- ``dutch_allocation._round_stakes``  … 旧ランクの入稿（1点上限あり）
- ``stake_allocation.allocate_budget`` … 旧ランクの相手別配分（最低1口保証）
- ``type_lab._allocate_once``          … 型ラボ（0円点を落として配り直す）

**同じ入力で3つの出力が一致するのは 5.1% だけ**（無作為3,000件で実測。
A↔B 75.0% / A↔C 93.4% / B↔C 85.2% が不一致）。つまりこれらは
「重複した実装」ではなく **契約の違う3つの配分方針**である。

| | 最低1口保証 | 1点上限 | 端数の寄せ先 | 0円の点 |
|---|---|---|---|---|
| ``FLOOR_THEN_LOWEST_PAYOUT`` | なし | あり | 想定払戻が最小の点 | 落とす |
| ``RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT`` | **あり** | なし | 口数/重み が最小の点 | 起きない |
| ``FLOOR_THEN_LARGEST_REMAINDER`` | なし | なし | 比例誤差が最大の点 | 呼び出し側が落として再配分 |

だから**丸ごと1つにはしない**。統合するのは配り方の骨格だけで、
方針の違いは引数で明示する。こうすると:

- 「端数をどこへ寄せるか」の判断が1ファイルに集まり、比較できる
- 片方だけ直して他方に反映し忘れる事故が減る
  （2026-08-28 の「0円点を返さない」修正は type_lab にしか入っていなかった）
- 3方針の違いが表になって、次に触る人が意図的な差だと分かる

## 端数の寄せ先について

⚠️ ``LOWEST_PAYOUT`` は **オッズを参照する**。入稿時点で分かっている朝オッズ／
   予測オッズに対して使う分には問題ないが、**確定オッズを渡してはいけない**
   （検証スクリプトで実際にやらかし、効果を 2pt 過大評価した記録がある。
   ``stake_allocation.allocate_budget`` の docstring を参照）。
   オッズを見ない ``LOWEST_UNITS_PER_WEIGHT`` は同じ狙いを重みだけで達成する。

振る舞いは ``tests/fixtures/stake_allocation_golden.json``（リファクタ前の
3実装の出力 400 件）で固定してある。ここを触ったら必ずそのテストを通すこと。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# 端数（比例配分の余り）をどの点へ寄せるか。
FLOOR_THEN_LARGEST_REMAINDER = "largest_remainder"
"""比例誤差（理論値 − 割当）が最大の点へ寄せる。オッズを見ない。"""

FLOOR_THEN_LOWEST_PAYOUT = "lowest_payout"
"""想定払戻（口数 × オッズ）が最小の点へ寄せる。保証を上げる方向にしか使わない。"""

RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT = "lowest_units_per_weight"
"""全点に1口ずつ確保してから、口数/重み が最小の点へ寄せる。オッズを見ない。"""


def distribute_units(
    weights: Mapping,
    n_units: int,
    *,
    leftover: str = FLOOR_THEN_LARGEST_REMAINDER,
    cap_units: int | None = None,
    payout_per_unit: Mapping | None = None,
    order: Sequence | None = None,
) -> dict:
    """重み ``weights`` に比例して ``n_units`` 口を配る。

    Args:
        weights: {キー: 重み}。ふつう 1/オッズ。
        n_units: 配る総口数（予算 ÷ 単位）。
        leftover: 端数の寄せ先。上の3定数のいずれか。
        cap_units: 1点あたりの上限口数。None なら上限なし。
        payout_per_unit: ``FLOOR_THEN_LOWEST_PAYOUT`` のときに必要（ふつうオッズ）。
        order: 反復順を固定したいときのキー列。省略時は ``weights`` の順。

    Returns:
        {キー: 口数}。``RESERVE_ONE`` 以外では 0 口が出うる（落とすかは呼び出し側の責務）。

    Raises:
        ValueError: 重みが空／合計が 0 以下／``RESERVE_ONE`` で口数が足りない場合。
    """
    keys = list(order) if order is not None else list(weights)
    if not keys:
        raise ValueError("weights が空です")
    total = sum(float(weights[k]) for k in keys)
    if total <= 0:
        raise ValueError("重みの合計が0以下です")

    if leftover == RESERVE_ONE_THEN_LOWEST_UNITS_PER_WEIGHT:
        if n_units < len(keys):
            raise ValueError(f"{n_units} 口では {len(keys)} 点に配分できません")
        units = {k: 1 for k in keys}
        rest = n_units - len(keys)
        for k in keys:
            units[k] += int(rest * float(weights[k]) / total)
        while sum(units.values()) < n_units:
            target = min(keys, key=lambda k: units[k] / max(float(weights[k]), 1e-12))
            units[target] += 1
        return units

    # 比例配分の切り捨てから始める（上限があればここで頭を打つ）
    units = {k: int(n_units * float(weights[k]) / total) for k in keys}
    if cap_units is not None:
        units = {k: min(v, cap_units) for k, v in units.items()}

    if leftover == FLOOR_THEN_LARGEST_REMAINDER:
        while sum(units.values()) < n_units:
            room = [k for k in keys if cap_units is None or units[k] < cap_units]
            if not room:
                break
            target = max(room, key=lambda k: n_units * float(weights[k]) / total - units[k])
            units[target] += 1
        return units

    if leftover == FLOOR_THEN_LOWEST_PAYOUT:
        if payout_per_unit is None:
            raise ValueError("FLOOR_THEN_LOWEST_PAYOUT には payout_per_unit が必要です")
        while sum(units.values()) < n_units:
            room = [k for k in keys if cap_units is None or units[k] + 1 <= cap_units]
            if not room:
                break
            target = min(room, key=lambda k: units[k] * float(payout_per_unit[k]))
            units[target] += 1
        return units

    raise ValueError(f"未知の leftover 指定です: {leftover!r}")
