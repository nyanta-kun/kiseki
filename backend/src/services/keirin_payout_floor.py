"""商品としての「最低払戻（下振れ時）」の正本（2026-08-29 新設）。

## 何を直したか

レビュー画面は `min(賭け金 × odds_low)` を
**「確定までにオッズが下振れした場合の払戻（下側25%分位）。承認判断は
こちらを見てください」**と出していた。だが `odds_low` は
**1点あたり**の下側25%分位（`keirin/src/odds_prediction.py` の
`conservative_multiplier`）で、そこから最小値を取ると
**買う点数が増えるほど甘くなる**（順序統計量）。しかも傾斜配分は
払戻をそろえるほど点を接近させるので、最小はさらに深く食い込む。

実測（honest 2026・15,253R）で**確定がその額を割った確率**:

| 点数 | 2 | 3 | 4 | 5 | 6 | 8 |
|---|---|---|---|---|---|---|
| 三連複7車 | 30.6% | 48.3% | 63.6% | **74.5%** | 82.0% | 89.8% |
| 三連単7車 | 45.2% | 58.1% | 67.3% | 74.5% | 79.4% | 86.3% |

**25% なのは1点のときだけ。** 5点の商品では4回に3回割っていた。
実入稿でも同じ「最低1.5倍」の看板の達成率が 3点 95.6% / 5点 55.6% と
点数でバラバラだった。

そこで **k点の最小値そのものの下側25%分位** を測り直したのがこの表。

## 使い方の境界（混ぜないこと）

| 量 | 正本 | 用途 |
|---|---|---|
| 1点あたりの下振れ | keirin `conservative_multiplier(n_car)` | `bet_detail.odds_low`（1点ごとの列） |
| **k点の最小の下振れ** | **ここ** | 商品の「最低払戻」表示・ガミ判定 |

🔴 **入稿ゲート（`MIN_EXPECTED_PAYOUT_*` の 1.5倍）はここを使っていない。**
   ゲートの倍率は商品内で一律なので `fp × c >= 1.5` は `fp >= 1.5/c` と同値＝
   **閾値の付け替えにすぎず、実体は「計画の最低払戻 >= 1.78倍」**。
   ここを入れると足切りが強くなり、落ちるのは的中率もROIも高い側だった
   （実測 39.7%/82.6% ↔ 残る側 24.0%/70.9%）。**表示と判定は別の判断**。
   数値と経緯は `keirin/docs/oddspred_gap_2026_08_29.md`。

## 表の作り方（再現）

`keirin/scripts/exp_oddspred_gap/07_floor_ck_verify.py` /
残差キャッシュは同 `03b_build_resid.py`。honest 窓（2026-01〜08）で、
レース内の安い順 k点を本番と同じダッチ配分（`allocate_budget`）にし、
`確定の最低払戻 ÷ 計画の最低払戻` の p25 を取る。

- **プランの形にほぼ依らない**（k=5 で 安い順 0.638 / 軸2車総流し 0.652）
- **車数にもほぼ依らない**（k=5 で 7車 0.638 / 9車 0.658）→ 券種ごとに1本の表で足りる。
  同じ k では**小さい方**（保守側）を採ってある
- **月をまたいでも安定**（k=5 の相対値が 8か月で 0.682〜0.748。k の効き
  0.80→0.70→0.62 より小さい）

⚠️ 再学習で予測オッズモデルが変わったら測り直すこと（表は特定のモデルの
   誤差分布に紐づく）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: 券種 → {買う点数: 「確定の最低払戻 ÷ 計画の最低払戻」の下側25%分位}
#: 🔴 **1点あたりの分位ではない。** k点の最小そのものの分位。
FLOOR_RATIO_BY_POINTS: dict[str, dict[int, float]] = {
    "trio": {1: 0.917, 2: 0.811, 3: 0.737, 4: 0.680, 5: 0.638, 6: 0.607, 7: 0.585,
             8: 0.568, 9: 0.554, 10: 0.544, 11: 0.536, 12: 0.530, 13: 0.524, 14: 0.519},
    "trifecta": {1: 0.821, 2: 0.702, 3: 0.642, 4: 0.606, 5: 0.579, 6: 0.560, 7: 0.544,
                 8: 0.534, 9: 0.523, 10: 0.512, 11: 0.506, 12: 0.498, 13: 0.492, 14: 0.487},
}

#: 券種の日本語表記（`bet_detail.lines[].bet_type`）→ 表のキー。
BET_TYPE_TO_KIND: dict[str, str] = {"3連複": "trio", "3連単": "trifecta"}

DEFAULT_KIND = "trio"


def floor_ratio(n_points: int, bet_kind: str = DEFAULT_KIND) -> float:
    """買う点数 `n_points` の商品で、確定の最低払戻が計画の何倍まで落ちるか（p25）。

    ⚠️ 表に無い点数は**一番近い端の値へ丸める**。14点より多い商品は実在しない
       （最大は型ラボの三連単14点）が、増えたときに例外で画面を落とさないため。
       ただし k が大きいほど本当の値は下がり続けるので、**丸めは楽観側**。
       点数が増えたら表を測り直すこと。
    """
    table = FLOOR_RATIO_BY_POINTS.get(bet_kind) or FLOOR_RATIO_BY_POINTS[DEFAULT_KIND]
    if n_points < 1:
        raise ValueError(f"点数が不正です: {n_points}")
    if n_points in table:
        return table[n_points]
    return table[max(table)] if n_points > max(table) else table[min(table)]


def bet_kind_of(lines: Sequence[Mapping]) -> str:
    """買い目の券種。混在（実在しない）なら三連単側＝厳しい方へ倒す。"""
    kinds = {BET_TYPE_TO_KIND.get(str(x.get("bet_type")), DEFAULT_KIND) for x in lines}
    return "trifecta" if "trifecta" in kinds else DEFAULT_KIND


def min_payout_floor(lines: Sequence[Mapping]) -> float | None:
    """**下振れしても割らない**最低払戻（円）。測れないなら None。

    `lines` は `netkeirin_submissions.bet_detail` の `lines`
    （`stake` / `odds` / `odds_low` / `bet_type` / `odds_source`）。

    - 全点が予測オッズなら **min(賭け金 × オッズ) × floor_ratio(点数, 券種)**。
      これが「下側25%分位」を名乗れる唯一の形
    - 板が混ざる古い記録（2026-08-26 以前）は、板が買う帯で系統的に高いので
      k 補正を掛けると**楽観を残したまま緩める**ことになる。従来どおり
      `min(賭け金 × odds_low)` を返す（そちらのほうが小さい）
    - `odds_low` が全点に無ければ None（＝下限側では測れなかった）
    """
    if not lines:
        return None
    if any(x.get("odds_low") in (None, 0) or not x.get("odds") or not x.get("stake")
           for x in lines):
        return None
    legacy = min(float(x["stake"]) * float(x["odds_low"]) for x in lines)
    if not all(x.get("odds_source") == "predicted" for x in lines):
        return legacy
    plan = min(float(x["stake"]) * float(x["odds"]) for x in lines)
    return plan * floor_ratio(len(lines), bet_kind_of(lines))
