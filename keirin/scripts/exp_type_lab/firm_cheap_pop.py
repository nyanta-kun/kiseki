#!/usr/bin/env python3
"""§6-2: 「三連単が安い」の定義 A / B の母集団と一致率（2026-09-11）。

A = 指数1-2-3位（p3 降順）の三連単**予測**オッズ
B = 印 ◎-○-△（`A_prediction_mark` 1/2/3）の三連単**予測**オッズ
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402

Z = {k: v for k, v in np.load("/tmp/race_type_board.npz", allow_pickle=True).items()}
CIDX = C.CIDX


def series(idx):
    P3, MK, PO = Z["P3"], Z["A_prediction_mark"], Z["PO"]
    a, b = [], []
    for i in idx:
        order = tuple(int(c) for c in np.argsort(-P3[i]) + 1)[:3]
        a.append(float(PO[i][CIDX[order]]))
        mk = MK[i]
        try:
            hon = int(np.flatnonzero(mk == 1)[0]) + 1
            tai = int(np.flatnonzero(mk == 2)[0]) + 1
            ana = int(np.flatnonzero(mk == 3)[0]) + 1
            b.append(float(PO[i][CIDX[(hon, tai, ana)]]))
        except IndexError:
            b.append(np.nan)
    return np.array(a), np.array(b)


def main() -> None:
    for win in ("all", "explore", "confirm"):
        idx = C.select(None, win)
        idx = np.array([i for i in idx if str(Z["TYPE"][i]) in "ABCDEF"])
        a, b = series(idx)
        ok = np.isfinite(a) & (a > 0)
        okb = np.isfinite(b) & (b > 0)
        print(f"\n=== {win}  {len(idx):,}R  (A有効 {ok.sum():,} / B有効 {okb.sum():,}) ===")
        print(f"  中央  A {np.median(a[ok]):6.2f}倍   B {np.median(b[okb]):6.2f}倍")
        print(f"  {'閾値':>6s} | {'A件':>6s} {'A%':>6s} | {'B件':>6s} {'B%':>6s}"
              f" | {'両方':>6s} {'A∩B/A':>7s}")
        for t in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
            ma, mb = ok & (a < t), okb & (b < t)
            both = ma & mb
            print(f"  {t:6.1f} | {ma.sum():6,} {ma.mean()*100:5.2f}%"
                  f" | {mb.sum():6,} {mb.mean()*100:5.2f}%"
                  f" | {both.sum():6,} {both.sum()/max(ma.sum(),1)*100:6.1f}%")
        # 印順=指数順 の一致率
        same = 0
        for i in idx:
            mk = Z["A_prediction_mark"][i]
            o = tuple(int(c) for c in np.argsort(-Z["P3"][i]) + 1)[:3]
            try:
                m = (int(np.flatnonzero(mk == 1)[0]) + 1,
                     int(np.flatnonzero(mk == 2)[0]) + 1,
                     int(np.flatnonzero(mk == 3)[0]) + 1)
            except IndexError:
                continue
            same += (o == m)
        print(f"  印順(◎○△)==指数順: {same/len(idx)*100:.2f}%")


if __name__ == "__main__":
    main()
