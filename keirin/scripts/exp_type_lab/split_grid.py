#!/usr/bin/env python3
"""第9章（差し替え）型の格子は**決着そのもの**を切り分けているか（2026-09-10）。

🔴 **paper 行では境界を動かせない。** `plans_for` は「その型の商品」しか組まないので、
   `type_lab_picks` にはそのレースの**型の商品しか無い**（実測: 型A の行は
   A_hit/A_pay/A_trio/A_ana/A_sign のみ）。境界を動かして型を変えると
   その型の行が存在せず、**そのレースが台から消える**——件数の増減はデータの穴で
   あって効果ではない。境界の検証は**商品を通さず決着で**行う。

台: `/tmp/race_type_board.npz`（7車・vintage walk-forward の p3）。
指標は商品に依らない3つ:
  二軸そろい  … p3 上位2車が**両方**3着以内（＝軸2車前提の商品が成立する条件）
  順当        … p3 上位3車の集合＝実際の3着以内（＝絞れる条件）
  三連複配当  … 中央値（＝帯・看板の原資）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.common import CANON3, board  # noqa: E402
from src.type_lab import AXIS_SUM_FIRM  # noqa: E402


def prep():
    z = board()
    m = (z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
    idx = np.flatnonzero(m)
    p3 = z["P3"][idx]
    order = np.argsort(-p3, axis=1) + 1
    win = np.array([set(CANON3[int(w)]) for w in z["TRIO_WIN"][idx]], dtype=object)
    a1, a2, a3 = order[:, 0], order[:, 1], order[:, 2]
    both = np.array([(a1[i] in win[i]) and (a2[i] in win[i]) for i in range(len(idx))])
    junto = np.array([{a1[i], a2[i], a3[i]} == win[i] for i in range(len(idx))])
    return dict(date=z["DATE"][idx], axis=z["AXIS_SUM"][idx].astype(float),
                arare=z["ARARE"][idx].astype(int), tl=z["TYPE"][idx],
                rtype=z["RTYPE"][idx], dayidx=z["DAYIDX"][idx].astype(int),
                both=both, junto=junto, pay=z["TRIO_PAY"][idx].astype(float))


def wsel(D, w):
    d = D["date"]
    return ((d >= "2024-07-01") & (d <= "2025-12-31") if w == "e"
            else (d >= "2026-01-01"))


def cell(D, m):
    n = int(m.sum())
    if n < 100:
        return None
    return dict(n=n, both=D["both"][m].mean() * 100, junto=D["junto"][m].mean() * 100,
                pay=float(np.median(D["pay"][m])))


def main():
    D = prep()
    W = (("探索 2024-07〜2025-12", "e"), ("確認 2026-01〜08", "c"))
    print("=" * 118)
    print("■ 第9章 2×3 の格子は決着を切り分けているか（商品を通さない指標）")
    print("=" * 118)
    for lab, w in W:
        base = wsel(D, w)
        print(f"\n── {lab}   全体 二軸そろい {D['both'][base].mean()*100:.2f}% / "
              f"順当 {D['junto'][base].mean()*100:.2f}% / 三連複中央 "
              f"{np.median(D['pay'][base]):.1f}倍")
        print(f"    {'型':4s}{'n':>7s}{'二軸そろい':>10s}{'順当':>8s}{'三連複中央':>10s}")
        for t in "ABCDEF":
            c = cell(D, base & (D["tl"] == t))
            if c:
                print(f"    {t:4s}{c['n']:7d}{c['both']:9.2f}%{c['junto']:7.2f}%"
                      f"{c['pay']:9.1f}倍")
        print(f"\n    格子（行=力関係 axis_sum・列=荒れ arare）: 二軸そろい% / 順当%")
        bands = [("<1.30", -9e9, 1.30), ("1.30-1.44", 1.30, 1.44),
                 ("1.44-1.58", 1.44, 1.58), (">=1.58", 1.58, 9e9)]
        print(f"      {'axis帯':12s}" + "".join(f"{s:>22s}" for s in
                                                ("s<=-1(荒れにくい)", "s==0", "s>=1(荒れやすい)")))
        for bl, lo, hi in bands:
            row = f"      {bl:12s}"
            for cond in (D["arare"] <= -1, D["arare"] == 0, D["arare"] >= 1):
                c = cell(D, base & (D["axis"] >= lo) & (D["axis"] < hi) & cond)
                row += (f"{c['both']:9.1f}%/{c['junto']:5.1f}%(n={c['n']:5d})" if c
                        else f"{'—':>22s}")
            print(row)

    # 境界の掃引（決着ベース）
    print("\n" + "=" * 118)
    print("■ 第9章b 力関係の境界をどこに置くと決着が最もよく分かれるか（商品を通さない）")
    print("=" * 118)
    print(f"    {'境界':8s}{'堅い側%':>8s}" + "".join(
        f"{'  堅い そろい':>14s}{'混戦 そろい':>12s}{'分離':>8s}" for _ in W))
    for thr in (1.30, 1.34, 1.38, 1.42, AXIS_SUM_FIRM, 1.46, 1.50, 1.54, 1.58):
        cells = ""
        share = None
        for _lab, w in W:
            base = wsel(D, w)
            f_ = base & (D["axis"] >= thr)
            g_ = base & (D["axis"] < thr)
            if share is None:
                share = f_.sum() / base.sum() * 100
            a, b = D["both"][f_].mean() * 100, D["both"][g_].mean() * 100
            cells += f"{a:13.2f}%{b:11.2f}%{a-b:+7.2f}pt"
        mark = "★" if thr == AXIS_SUM_FIRM else " "
        print(f"    {thr:.2f}{mark:3s}{share:7.1f}%{cells}")

    # arare は axis の上に何を足しているか
    print("\n" + "=" * 118)
    print("■ 第9章c 荒れ(arare) は力関係(axis_sum) の上に情報を足しているか")
    print("=" * 118)
    for lab, w in W:
        base = wsel(D, w)
        q = np.percentile(D["axis"][base], [20, 40, 60, 80])
        print(f"\n── {lab}   （axis 五分位の中で arare を見る・二軸そろい%）")
        print(f"      {'axis五分位':12s}" + "".join(
            f"{s:>16s}" for s in ("s<=-1", "s==0", "s>=1", "s>=1 − s<=-1")))
        for i in range(5):
            lo = -9e9 if i == 0 else q[i - 1]
            hi = 9e9 if i == 4 else q[i]
            mm = base & (D["axis"] >= lo) & (D["axis"] < hi)
            vals = []
            for cond in (D["arare"] <= -1, D["arare"] == 0, D["arare"] >= 1):
                c = cell(D, mm & cond)
                vals.append(c["both"] if c else float("nan"))
            print(f"      Q{i+1:<11d}" + "".join(f"{v:15.2f}%" for v in vals)
                  + f"{vals[2]-vals[0]:+15.2f}pt")


if __name__ == "__main__":
    main()


def payout_grid():
    """第9章d 荒れ(arare) は**配当**を並べているか（力関係を固定して見る）。"""
    D = prep()
    print("\n" + "=" * 118)
    print("■ 第9章d 荒れ(arare) は配当を並べているか（axis 五分位の中で・三連複配当の中央値）")
    print("=" * 118)
    for lab, w in (("探索 2024-07〜2025-12", "e"), ("確認 2026-01〜08", "c")):
        base = wsel(D, w)
        q = np.percentile(D["axis"][base], [20, 40, 60, 80])
        print(f"\n── {lab}")
        print(f"      {'axis五分位':12s}" + "".join(
            f"{s:>16s}" for s in ("s<=-1", "s==0", "s>=1", "s>=1 ÷ s<=-1",
                                  "100倍+ の割合 差")))
        for i in range(5):
            lo = -9e9 if i == 0 else q[i - 1]
            hi = 9e9 if i == 4 else q[i]
            mm = base & (D["axis"] >= lo) & (D["axis"] < hi)
            med, big = [], []
            for cond in (D["arare"] <= -1, D["arare"] == 0, D["arare"] >= 1):
                s = mm & cond
                med.append(float(np.median(D["pay"][s])) if s.sum() >= 100 else float("nan"))
                big.append(float((D["pay"][s] >= 100).mean() * 100) if s.sum() >= 100 else float("nan"))
            print(f"      Q{i+1:<11d}" + "".join(f"{v:14.1f}倍" for v in med)
                  + f"{med[2]/med[0]:15.2f}x" + f"{big[2]-big[0]:+14.2f}pt")


if __name__ == "__main__":
    payout_grid()
