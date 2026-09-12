#!/usr/bin/env python3
"""機序の裏付け: 新スコアと axis_sum の判定が食い違うレースで、そろい率と商品成績を並べる。

「判定が正しくなっても商品成績が動かない」を、食い違い集合だけで直接見る。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin/scripts/exp_type_lab")
import common as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arms as A  # noqa: E402  (読み込み時に台を作るだけ)

T, P = A.T, A.P


def stats(rows):
    if not rows:
        return "n=0"
    n = len(rows); inv = sum(r["inv"] for r in rows); pay = sum(r["pay"] for r in rows)
    shown = sum(1 for r in rows if r["pay"] >= r["inv"]) / n * 100
    pays = sorted(r["pay"] for r in rows if r["pay"] > 0)
    med = np.median(pays) if pays else 0
    big = sum(1 for p in pays if p >= 100_000)
    return f"n={n:5d} 表示的中 {shown:6.2f}% 払戻中央 {med:7,.0f} 10万+ {big:3d} ROI {pay/inv*100:5.1f}"


def main() -> None:
    firm_cur = T.type.isin(list("ABC")).values
    for score in ("p_both", "s_full"):
        flip, firm_new = A.arm_firm(score)
        print(f"\n## {score} と axis_sum の判定が食い違うレース")
        for win, (lo, hi) in A.WINS.items():
            m = ((T.date >= lo) & (T.date <= hi)).values
            for direction, sel in (("新=堅い / 現行=混戦", flip & ~firm_cur & m),
                                   ("新=混戦 / 現行=堅い", flip & firm_cur & m)):
                idx = np.flatnonzero(sel)
                y = T.y.values[idx].mean() * 100
                agree_idx = np.flatnonzero(~flip & m & (firm_cur if "現行=堅い" in direction else ~firm_cur))
                y_agree = T.y.values[agree_idx].mean() * 100
                cur_rows = [P[int(T.i[r])]["cur"] for r in idx if P[int(T.i[r])]["cur"]]
                flp_rows = [P[int(T.i[r])]["flip"] for r in idx if P[int(T.i[r])]["flip"]]
                print(f"  {win} {direction}: {len(idx):5d}R  そろい {y:5.1f}% "
                      f"(一致側の同ラベル {y_agree:5.1f}%)  同ライン率 {T.same_line.values[idx].mean()*100:4.1f}%")
                print(f"      現行ラベルの商品: {stats(cur_rows)}")
                print(f"      新ラベルの商品  : {stats(flp_rows)}")

    print("\n## 参考: 型ごとの そろい率 × 同ライン（全期間）")
    g = T.groupby(["type", "same_line"]).y.agg(["mean", "size"])
    print((g["mean"] * 100).round(1).unstack().to_string())
    print((g["size"]).unstack().to_string())
    print("\n## 参考: 売った商品の軸2そろい率（現行）と s_full 五分位（確認窓）")
    m = (T.date >= "2026-01-01").values
    e = T[m].copy()
    e["q"] = pd.qcut(e.s_full, 5, labels=False)
    for q, sub in e.groupby("q"):
        rows = [P[int(i)]["cur"] for i in sub.i if P[int(i)]["cur"]]
        print(f"  Q{q+1}: そろい {sub.y.mean()*100:5.1f}%  {stats(rows)}")


if __name__ == "__main__":
    main()
