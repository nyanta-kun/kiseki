#!/usr/bin/env python3
"""6並びの top-k カバレッジ（k=1,2,3）。商品は1集合あたり複数の並びを買うので、
top-1 の改善がそのまま商品に効くとは限らない。どこで差が消えるかを見る。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fit as FT  # noqa: E402
from build_ds import SETS  # noqa: E402


def cov(s, y, k):
    r = (-s).argsort(1)
    return float(np.any(r[:, :k] == y[:, None], 1).mean())


def main() -> None:
    for tag, tr, ev in FT.FOLDS:
        params = {nm: FT.fit_cl(SETS[nm], tr) for nm in ("L1_pair4", "L4_inter")}
        y = FT.Y[ev]
        S = {a: FT.score_of(a, None, ev) for a in ("base", "prod_fwd", "prod_rev", "mkt")}
        for nm, p in params.items():
            S[nm] = FT.score_of(nm, p, ev)
        print(f"\nfold {tag}  n={int(ev.sum()):,}")
        print(f"{'腕':<12}{'top1':>8}{'top2':>8}{'top3':>8}")
        for a in ("base", "prod_fwd", "prod_rev", "mkt", "L1_pair4", "L4_inter"):
            print(f"{a:<12}" + "".join(f"{cov(S[a], y, k)*100:7.2f}%" for k in (1, 2, 3)))
        print(f"{'一様':<12}{100/6:7.2f}%{200/6:7.2f}%{300/6:7.2f}%")


if __name__ == "__main__":
    main()
