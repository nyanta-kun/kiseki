"""7C/7M1 のレース選別スコア（2026-08-18 新設）。

## なぜ差し替えるのか

7C の選別は長らく **「モデル3着内率の上位2車の合計 >= 1.44」という 1次元**だった。
だが 7C の目的関数は**二軸的中（軸2車がともに3着内）**であり、合計値は
「上位2車がどれだけ強いか」しか見ていない。実際、外れの主因は
**「軸2だけ来ず」27%**（memory `keirin_axis_miss_anatomy_2026_08_17`）で、
合計が同じでも **2位と3位が僅差なら軸2の指定が危うい**。

そこで二軸的中を目的変数にしたロジスティック回帰を当て、
**同じ採用件数**でどれだけ当たる帯を選べるかを測った（`docs/analysis/56-race-selection-meta.md`）:

| 窓 | 二軸的中の差（同件数） |
|---|---|
| VAL 2025-07〜2026-02 | **+2.08pt [+1.39, +2.74]** |
| TEST 2026-03〜07-15 | **+1.46pt [+0.52, +2.39]** |
| 確認窓 2026-07-16〜08-18 | +1.49pt [−0.82, +3.32] |

## 4特徴

| 特徴 | 意味 | 係数の向き |
|---|---|---|
| `sum2` | 上位2車の3着内率の合計（＝**旧ゲートそのもの**） | + |
| `gap23` | **2位と3位の差**。軸2が本当に2番手か | + |
| `same_line` | **軸2車が同ライン**か | + |
| `p_ent` | レース全体の混戦度（3着内率のエントロピー） | − |

`gap23` と `same_line` が旧ゲートの穴。`same_line` が独立に効くのは、
ペア相関（同ライン）が**選別側でも拾える**ということ
（memory `keirin_layer2_pair_ceiling_2026_08_10` の層2の一部をここで回収している）。

## 🔴 使うときの約束

- **入力は較正後の3着内率**（`p3_calibration.calibrate_top3` を通した値）。
  旧ゲートが較正値を見ていたのと土俵を揃えるため。生値で当てると閾値がずれる。
- **7車立て専用**。`p_ent` は車数に依存するので 9車へそのまま持ち込めない
  （9C は 9車で当て直すこと。7C の定数を 9車へ移植して壊した前例がある——
  memory `keirin_rank_9c_design_2026_08_14`）。
- **閾値は「旧ゲートと同じ通過率」になる点**を推定窓で求めたもの。
  母集団を絞る/広げる意図はない。**件数を変えたいときだけ動かす**。
- 係数は**固定値を焼き込んだままにしない**。モデルを再学習すると 3着内率の分布が
  動くので、`scripts/fit_race_gate_7c.py` で引き直す（`p3_calibration` と同じ運用）。

## ⚠️ 7M1 との関係

7M1 は「7C が取らない混戦」＝**この判定の裏返し**で定義されている。
**片方だけ差し替えると両ランクの境界に穴か重なりができる**ので、
必ず同じ関数（`race_gate_7c.passes`）を通すこと。
"""
from __future__ import annotations

import math

from .p3_calibration import calibrate_top3

# 係数の推定窓。引き直したらここも更新する（どの期間の較正かが追えなくなる）。
FIT_WINDOW = "2025-01-01〜2025-06-30"

N_CARS = 7                      # 7車立て専用（下記 p_ent は車数に依存する）

COEF: dict[str, float] = {
    "sum2":      1.6496,
    "gap23":     1.6313,
    "same_line": 0.6352,
    "p_ent":    -4.0709,
}
INTERCEPT = 4.4917

# 旧ゲート（較正後の上位2車合計 >= 1.44）と**同じ通過率**になる点。
# 推定窓の通過率 50.9% → この閾値。VAL 51% / TEST 53% / 確認窓 49% で再現した。
THRESHOLD = 0.1099


def features(top3_probs: dict[int, float],
             line_groups: dict[int, object] | None,
             race_type: str | None,
             cup_grade: int | None) -> dict[str, float] | None:
    """4特徴を作る。`top3_probs` は **0-1 スケールの生値**（ここで較正する）。

    line_groups: {frame_no: ライン識別子}。取れない場合は `same_line=0` として扱う
      （**黙って落とさない**。ラインが取れない日にゲートが全滅するのを防ぐ）。
    returns None … 車数が足りない/値が無い（判定不能。呼び出し側で従来判定へ落とす）
    """
    if not top3_probs or len(top3_probs) < 3:
        return None
    cal = {f: calibrate_top3(p, race_type, cup_grade)
           for f, p in top3_probs.items() if p is not None}
    if len(cal) < 3:
        return None
    ranked = sorted(cal, key=lambda f: (-cal[f], f))
    a1, a2, a3 = ranked[0], ranked[1], ranked[2]
    total = sum(cal.values())
    if total <= 0:
        return None
    ent = -sum((v / total) * math.log(v / total + 1e-12) for v in cal.values())
    lg = line_groups or {}
    g1, g2 = lg.get(a1), lg.get(a2)
    same = 1.0 if (g1 is not None and g2 is not None and str(g1) == str(g2)) else 0.0
    return {
        "sum2": cal[a1] + cal[a2],
        "gap23": cal[a2] - cal[a3],
        "same_line": same,
        "p_ent": ent,
    }


def score(top3_probs: dict[int, float],
          line_groups: dict[int, object] | None,
          race_type: str | None,
          cup_grade: int | None) -> float | None:
    """選別スコア（ロジットのまま返す。閾値 `THRESHOLD` と比較する）。"""
    f = features(top3_probs, line_groups, race_type, cup_grade)
    if f is None:
        return None
    return INTERCEPT + sum(COEF[k] * f[k] for k in COEF)


def passes(gate_score: float | None, threshold: float = THRESHOLD) -> bool:
    """スコアが閾値以上か。`None`（判定不能）は False。

    ⚠️ 呼び出し側は「キーが無い候補」と「判定不能」を区別すること。
       旧形式の候補JSON（キー自体が無い）は**従来の p3合計ゲートへ落とす**のが正しく、
       ここで False を返して全滅させてはいけない。
    """
    if gate_score is None:
        return False
    return float(gate_score) >= threshold
