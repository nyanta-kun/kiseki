"""競輪の確率をレース内で正規化する（2026-09-21 新設）。**正本はここ**。

`marquee` / `cup_grade` / `p3_calibration` と同じ手口で、kiseki 側を正本にして
keirin 側（`keirin/src/prob_normalize.py`）がファイル読み込みで束縛する。
🔴 **係数も式もここにしか書かない。** 写した瞬間に、ゲートが見る値と画面に出る値がずれる。
🔴 **標準ライブラリ以外を import しない。** keirin は FastAPI も numpy も無い venv から
   このファイルを直接読むため、依存を足すと Web は無事なまま入稿だけが落ちる。

## なぜ要るのか（2026-09-21 実測）

3着内率モデル `lgbm_wt_eval` も1着率モデル `lgbm_wt_win` も **レース内で正規化しない
二値分類器**で、しかも **入力に「そのレースが何車立てか」を持たない**
（`FEATURE_COLS_WT` に `n_entries` 相当の列が無い）。結果、定義上は
「3着内率の合計 = 3.0」「1着率の合計 = 1.0」になるはずのものが、車数で単調にずれる:

    車数   Σp3（理想 3.0）        Σpw（理想 1.0）
     5車   2.770 / 2.811  −7.7 / −6.3%   0.919 / 0.955  −8.1 / −4.5%
     6車   2.912 / 2.926  −2.9 / −2.5%   0.985 / 0.993  −1.5 / −0.7%
     7車   3.021 / 3.028  +0.7 / +0.9%   1.016 / 1.011  +1.6 / +1.1%
     8車   3.094 / 3.098  +3.1 / +3.3%   1.049 / 1.045  +4.9 / +4.5%
     9車   3.149 / 3.180  +5.0 / +6.0%   1.074 / 1.080  +7.4 / +8.0%
                                （左=探索2025 / 右=確認2026・両窓で単調）

（`wt_entries.pred_top3_pct` / `pred_win_pct`＝月次凍結 vintage の honest OOS。
その場で学習し直したモデルでも同じ形が出る＝保存値の都合ではない
`keirin/scripts/exp_field_size_ab.py`: 7車 3.014/3.019 ↔ 9車 3.181/3.207。
車数を特徴に足しても 9車 3.08/3.12 までしか縮まない＝目的関数に Σ 制約が無いので当然）

🔴 **保存値は 0-100 スケール**（`pred_*_pct`）。`if v > 1.5: v /= 100` のような
   「大きい値だけ割る」変換をしてはいけない。`pred_win_pct` は 25%tile が 2.6 で
   **1.5 未満の行が多数ある**ため、その規則だと一部だけ 100 倍のまま残り、
   確率として評価すると LogLoss が 0.31 → 1.75 に化ける（2026-09-21 に実際に踏んだ）。

## 正規化すると確率としての当てはまりは良くなる（小さいが両窓一貫）

`Brier` は 3着内率・1着率とも**全セル・両窓で改善**（p3 −0.0005〜−0.0037 /
pw −0.0004〜−0.0022）。`LogLoss` は **pw は全セル改善**（−0.0016〜−0.0058）だが、
**p3 は 5車・6車で悪化**（+0.002〜+0.020）＝ 少数車は Σ を 3 に引き上げる方向の
補正が効きすぎる。**商品KPI は動かない**（§10.4）ので、効果は「測定の正しさ」に限る。

**レース内の順位は単調変換なので絶対に変わらない。** 壊れるのは

- p3 / pw の**絶対値**をレース間共通の定数と比べるゲート
- 確率として**足し算・掛け算**する計算（期待値・ダッチ配分・信頼度の百分率）

## ⚠️ 適用していい場所・いけない場所

🔴 **既存のゲートの閾値を、正規化した値へ黙って差し替えてはいけない。**
   閾値は生の値の上で掃引して決めた定数で、正規化すると別のゲートになる。
   実測（型ラボ・7車・vintage・`keirin/scripts/exp_type_lab/sump3_norm2.py`）:

     腕                          表示的中 探索/確認     ROI 探索/確認
     現行（生 1.44）              24.17 / 24.08%      77.0 / 81.2
     正規化・閾値そのまま           23.88 / 23.75%      76.2 / 80.1   ← 両窓とも悪化
     正規化・同率閾値 1.423         24.17 / 23.91%      76.8 / 82.0   ← 無作為対照の中

   ＝ **型ラボの `AXIS_SUM_FIRM` には入れない**（2026-09-21 判断）。

🟢 **入れてよいのは「今まで正しくなかった計算」の側**:
   - 検証・分析スクリプト（レース間で確率を比べる全部）
   - 百分率として見せる値（合計を母数にする定義のもの）
   - 新しく作るゲート（最初から正規化した値の上で閾値を決める）
"""
from __future__ import annotations

from collections.abc import Mapping

#: 3着内の枠数。競輪は最少5車立てでも3着まであるので定数。
TOP3_SLOTS = 3
#: 1着の枠数。
WIN_SLOTS = 1
#: クリップの端。0/1 ちょうどを避ける（対数・オッズ換算で無限大になるため）。
PROB_EPS = 1e-6


def normalize_to_slots(probs: Mapping[int, float], slots: float) -> dict[int, float]:
    """レース内の確率を `Σp = slots` へ揃える。

    🔴 **クリップのみで、クリップ後の再正規化はしない。** JRA v28 の
    `normalize_place_to_slots` と同じ判断（`backend/src/indices/composite.py`）。
    1 を超える車が出ても押し戻さず、崩れをその1車に閉じ込める。押し戻すと
    その分が他車の確率を動かし、1車の外れ値がレース全体を汚すため。

    合計が 0 以下（全車 0・空）のときは**何もしない**。均等割りにすると
    「情報が無い」を「全車同じ確率」という**偽の情報**に変えてしまう。

    >>> normalize_to_slots({1: 0.6, 2: 0.6, 3: 0.6, 4: 0.6, 5: 0.6, 6: 0.6, 7: 0.6}, 3)
    {1: 0.42857142857142855, 2: 0.42857142857142855, 3: 0.42857142857142855, \
4: 0.42857142857142855, 5: 0.42857142857142855, 6: 0.42857142857142855, \
7: 0.42857142857142855}
    >>> normalize_to_slots({}, 3)
    {}
    >>> normalize_to_slots({1: 0.0, 2: 0.0}, 3)
    {1: 0.0, 2: 0.0}
    """
    if not probs or slots <= 0:
        return dict(probs)
    total = sum(float(v) for v in probs.values())
    if total <= 0:
        return {k: float(v) for k, v in probs.items()}
    f = slots / total
    return {k: min(max(float(v) * f, PROB_EPS), 1.0 - PROB_EPS) for k, v in probs.items()}


def normalize_top3(probs: Mapping[int, float]) -> dict[int, float]:
    """3着内率を `Σ = 3.0` へ揃える。

    >>> round(sum(normalize_top3({1: .5, 2: .5, 3: .5, 4: .5, 5: .5, 6: .5, 7: .5}).values()), 9)
    3.0
    """
    return normalize_to_slots(probs, TOP3_SLOTS)


def normalize_win(probs: Mapping[int, float]) -> dict[int, float]:
    """1着率を `Σ = 1.0` へ揃える。

    >>> round(sum(normalize_win({1: .4, 2: .4, 3: .4}).values()), 9)
    1.0
    """
    return normalize_to_slots(probs, WIN_SLOTS)


def slot_share(probs: Mapping[int, float], cars: tuple[int, ...] | list[int],
               slots: float = TOP3_SLOTS) -> float | None:
    """指定した車の確率が、そのレースの枠数のうち占める割合（0〜1）。

    ⚠️ **レース間で比べてよいのはこの値**。生の合計は Σ がレースごとに違うので
       「同じ 1.44 でも実質的な厳しさがレースによって違う」（`strategy_wt.py` の
       既存コメント「ゲートの正規化が不統一」と同じ話）。

    >>> round(slot_share({1: .6, 2: .6, 3: .6, 4: .4, 5: .3, 6: .3, 7: .2}, (1, 2)), 9)
    0.4
    """
    if not probs or not cars:
        return None
    total = sum(float(v) for v in probs.values())
    if total <= 0:
        return None
    return sum(float(probs[c]) for c in cars if c in probs) / total


def top2_slot_sum(probs: Mapping[int, float]) -> float | None:
    """上位2車の3着内率合計を、`Σ = 3.0` 換算で返す（＝正規化後の `axis_sum`）。

    🔴 **型ラボの `AXIS_SUM_FIRM = 1.44` はこの値と比べてはいけない**
       （閾値は生の値の上で決めてあり、商品KPIでは改善しないと実測済み）。
       新しく閾値を決める分析でだけ使うこと。

    >>> top2_slot_sum({1: .6, 2: .6, 3: .6, 4: .4, 5: .3, 6: .3, 7: .2})
    1.2
    """
    if not probs or len(probs) < 2:
        return None
    n = normalize_top3(probs)
    top2 = sorted(n, key=lambda c: (-n[c], c))[:2]
    return n[top2[0]] + n[top2[1]]
