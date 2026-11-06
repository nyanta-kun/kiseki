"""地方の勝率・3着内率をレース内で揃える（予想フロー再設計 Phase 1）。

DB にも FastAPI にも依存しない純関数。読み出し側の**唯一の正本**。

## なぜ要るか

`chihou_calculator.py` は is_win / is_top3 ヘッドの較正済み生確率をそのまま保存する。
レース内の合計は揃っておらず、前向き記録（2026-08-14〜10-31・3,128R）で
勝率の合計は平均 1.083（p10–p90 0.74〜1.44）、8頭以上の3着内率の合計は平均 3.171 だった。
1着は1頭・3着以内は3頭なので、合計が過大なぶんだけ確率を約束しすぎている。

同じ記録で、揃えた値の方が当たり具合が良い（馬ごとの対数損失・95%CI が 0 を跨がない）:

| | 生 → 揃えた値 | 差 [95%CI] |
|---|---|---|
| 単勝 | 0.26318 → 0.25903 | −0.00415 [−0.00448, −0.00382] |
| 3着内（8頭以上） | 0.48496 → 0.48019 | −0.00477 [−0.00539, −0.00410] |

合計が目標より小さいレースでも大きいレースでも改善している（縮小の副作用ではない）。
測定: `scripts/chihou_prob_normalize_ab.py` /
`docs/model_verification/chihou_prob_normalize_ab.json`。

## 規則

- 勝率は合計 1、3着内率は合計 3 に揃える。**頭数に依らず 3**（モデルの定義が「3着以内」のため。
  5〜7頭立ての複勝は2着までだが、ここで返すのは複勝確率ではなく3着内率）
- 揃えたあと 1 を超えた馬は 1 で止める（再正規化しない。JRA `normalize_place_to_slots` と同じ）
- 値が欠けている馬（None）は欠けたまま返し、合計の計算にも入れない
- 🔴 **取消・除外の馬は呼び出し側で除いてから渡す**（入れると他の馬の確率が下がる）
- 🔴 **保存値は変えない**。`chihou.calculated_indices` と前向き記録 `chihou.place_picks` には
  生の値を書き続ける（kiseki-lab が生値を前提に記録を読んでいる）
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping

WIN_TARGET_SUM = 1.0
TOP3_TARGET_SUM = 3.0


def scale_to_sum[K: Hashable](values: Mapping[K, float | None], target: float) -> dict[K, float | None]:
    """値の合計が ``target`` になるよう比例で揃え、1 を超えた分は 1 で止める。

    合計が 0 以下（全馬 0 または全馬欠損）のときは入力をそのまま返す。
    """
    present = {k: float(v) for k, v in values.items() if v is not None}
    total = sum(present.values())
    if total <= 0.0:
        return dict(values)
    factor = target / total
    return {k: (None if v is None else min(1.0, float(v) * factor)) for k, v in values.items()}


def normalize_win[K: Hashable](win: Mapping[K, float | None]) -> dict[K, float | None]:
    """勝率をレース内で合計 1 に揃える。"""
    return scale_to_sum(win, WIN_TARGET_SUM)


def normalize_top3[K: Hashable](top3: Mapping[K, float | None]) -> dict[K, float | None]:
    """3着内率をレース内で合計 3 に揃える（1 を超えた馬は 1）。"""
    return scale_to_sum(top3, TOP3_TARGET_SUM)
