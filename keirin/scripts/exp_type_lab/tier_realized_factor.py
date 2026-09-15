#!/usr/bin/env python3
"""段商品の入稿ゲートに使う「当たり目の 確定÷予測 の下側25%点」表を作る（2026-09-15）。

`src/type_lab.py` の `TIER_REALIZED_FACTOR` はこのスクリプトの出力をそのまま埋め込んだもの。
**値を引き直すときはここを回し、出力で定数を置き換える**（手で丸めない）。

## 何を測るか

当たった目の確定オッズは予測オッズより系統的に低い（予測帯が低いほど・同じ帯でも
当たり目は外れ目より1〜2割安い）。予測で 1.5万円を満たしても当たると 1.5万円を割るので、
ゲートを「予測 × 下振れ係数」で判定する。係数は**当たり目の 確定÷予測 の p25**。

## 台（検証で使ったものと同一）

- `vps_extract_tierskip.pkl` の `rows`（`keirin.type_lab_picks` の 7車・採点済み行を読み取りで抜いたもの）
  のうち `mode == 'paper'` ∧ `bt == 'trifecta'` ∧ 2025-01-01〜12-31 ∧ `hit` ∧ 勝ち目の予測オッズ `wpo`
  ∧ 確定オッズ `wtf` があるもの。**レース単位で最初の1件**（同じレースの複数プランを重ねない）。
  2025年の paper は vintage 予測オッズ（train_end 2024-12-31）＝ OOS。
- レース属性（種別 `RTYPE`・軸信頼 `AXIS_SUM`）は `/tmp/race_type_board.npz`。
  台に無いレースは使わない（検証も同じ。2026-09-15 実測で 7,567 中 1,693 レース）。

## セルと参照順

予測帯 [0,5) [5,10) [10,20) [20,40) [40,∞) × 種別（race_type が「チャレンジ」で始まる／他）×
段（axis_sum > 1.464 固め／> 1.353 広め／他 荒れ）。
参照順は (帯,種別,段) → (帯,*,段) → (帯,種別,*) → (帯,*,*)、**n >= 30 のセルだけ**使う。
p25 は `sorted(v)[int(0.25 * (len(v) - 1))]`。

pkl / npz はリポジトリに入れない（VPS DB と台から作る生成物）。

使い方:
    python scripts/exp_type_lab/tier_realized_factor.py --extract <pkl> --board /tmp/race_type_board.npz
"""
from __future__ import annotations

import argparse
import collections
import pickle

import numpy as np

BANDS = (5.0, 10.0, 20.0, 40.0)
MIN_N = 30
P = 0.25
AXIS_FIRM_MIN = 1.464
AXIS_MID_MIN = 1.353


def band_of(o: float) -> int:
    return sum(1 for b in BANDS if o >= b)


def q(v: list[float], p: float) -> float:
    v = sorted(v)
    return v[int(p * (len(v) - 1))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--board", default="/tmp/race_type_board.npz")
    a = ap.parse_args()

    x = pickle.load(open(a.extract, "rb"))
    z = np.load(a.board, allow_pickle=True)
    meta = {str(k): (str(r), float(s)) for k, r, s in zip(z["KEY"], z["RTYPE"], z["AXIS_SUM"])}

    seen: dict[str, tuple[float, float]] = {}
    for r in x["rows"]:
        if (r["mode"] == "paper" and r["bt"] == "trifecta"
                and "2025-01-01" <= r["d"] <= "2025-12-31"
                and r["hit"] and r["wpo"] and r["wtf"]):
            seen.setdefault(r["rk"], (r["wpo"], r["wtf"]))

    cell: dict[tuple, list[float]] = collections.defaultdict(list)
    no_meta = 0
    for rk, (po, fo) in seen.items():
        if rk not in meta:
            no_meta += 1
            continue
        rt, ax = meta[rk]
        b = band_of(po)
        ch = "challenge" if rt.startswith("チャレンジ") else "other"
        t = "firm" if ax > AXIS_FIRM_MIN else ("mid" if ax > AXIS_MID_MIN else "upset")
        for key in ((b, ch, t), (b, "*", t), (b, ch, "*"), (b, "*", "*")):
            cell[key].append(fo / po)

    print(f"# races={len(seen)} no_board={no_meta}")
    print("TIER_REALIZED_FACTOR: dict[tuple[int, str, str], tuple[int, float]] = {")
    for k in sorted(cell):
        v = cell[k]
        mark = "" if len(v) >= MIN_N else "  # n<30 使わない"
        line = f"    ({k[0]}, {k[1]!r}, {k[2]!r}): ({len(v)}, {q(v, P)!r}),"
        print(("# " + line.strip() + mark) if len(v) < MIN_N else line)
    print("}")


if __name__ == "__main__":
    main()
