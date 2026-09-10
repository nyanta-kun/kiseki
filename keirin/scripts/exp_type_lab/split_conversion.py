#!/usr/bin/env python3
"""第11章 型と商品の分業は成立しているか（2026-09-10）。

現行設計は 2段:
  ① 型（`race_shape`）が **軸2車が3着以内にそろうか**の水準を決める
  ② 商品（`sell_plans_for`）が **そろった前提から実際に拾う**（点数・帯・券種）

したがって商品の仕事の達成度は
    変換効率 = 表示的中率 ÷ その型の二軸そろい率
で見るのが設計に忠実。**表示的中を横並びにして「型Fが弱い」と読んではいけない。**
（外れの内訳＝②順序違い ③相手外し ④軸崩壊 は `miss_anatomy_2026_09_10.md` §1。
  ④軸崩壊は①型の層、②③は②商品の層で起きる。）
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.common import CANON3, board  # noqa: E402
from scripts.exp_type_lab.split_common import apply_cap, load_races, window  # noqa: E402


def board_outcome():
    z = board()
    m = (z["TRIO_WIN"] >= 0) & z["OKPRED"]
    idx = np.flatnonzero(m)
    p3 = z["P3"][idx]
    order = np.argsort(-p3, axis=1) + 1
    out = {}
    for j, i in enumerate(idx):
        w = set(CANON3[int(z["TRIO_WIN"][i])])
        a1, a2, a3 = order[j, 0], order[j, 1], order[j, 2]
        out[str(z["KEY"][i])] = (bool(a1 in w and a2 in w),
                                 bool({a1, a2, a3} == w),
                                 float(z["TRIO_PAY"][i]))
    return out


def main():
    OUT = board_outcome()
    R = load_races()
    print("=" * 122)
    print("■ 第11章 型と商品の分業（同じ母集団＝実際に売る商品の上で）")
    print("=" * 122)
    for wn, lab in (("explore", "探索 2025"), ("confirm", "確認 2026-01〜08")):
        sold = [r for r in apply_cap(window(R, wn))
                if r["settled"] and r["race_key"] in OUT]
        g = defaultdict(list)
        for r in sold:
            g[(r["tl"], r["plan"])].append(r)
        print(f"\n── {lab}   （母集団 {len(sold):,}件・板と突き合わせ）")
        print(f"    {'型/商品':16s}{'n':>6s}{'①二軸そろい':>12s}{'②表示的中':>10s}"
              f"{'変換効率':>9s}{'順当':>8s}{'三連複中央':>10s}{'点数':>6s}{'ROI':>7s}")
        for k in sorted(g):
            rows = g[k]
            if len(rows) < 60:
                continue
            both = np.mean([OUT[r["race_key"]][0] for r in rows]) * 100
            jun = np.mean([OUT[r["race_key"]][1] for r in rows]) * 100
            pay = float(np.median([OUT[r["race_key"]][2] for r in rows]))
            inv = np.array([r["inv"] for r in rows]); pv = np.array([r["pay"] for r in rows])
            sh = (pv >= inv).mean() * 100
            print(f"    {k[0]}/{k[1]:14s}{len(rows):6d}{both:11.2f}%{sh:9.2f}%"
                  f"{sh/both*100:8.1f}%{jun:7.2f}%{pay:9.1f}倍"
                  f"{np.mean([r['n_legs'] for r in rows]):6.1f}"
                  f"{pv.sum()/inv.sum()*100:6.1f}%")


if __name__ == "__main__":
    main()
