#!/usr/bin/env python3
"""位置別合成 PL（ライン隣接ボーナス**なし**）を全レース×210順列でベクトル化する。

本番 `src.strategy_wt.rank_7t3_blend_probs(..., adj_w=(1.0, 1.0))` と一致することを
`--verify` で確認する（一致しないまま使うと全部の数字が無効になる）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402

PERMS = C.CANON                      # 210 の (x,y,z)・車番 1..7
W = (1.0, 0.5, 0.0)                  # RANK_7T3_BLEND_W


def base_pl(PW: np.ndarray, P3: np.ndarray) -> np.ndarray:
    """(N,7) の pw / p3 から (N,210) の基礎 PL 確率。"""
    a = np.maximum(PW.astype(np.float64), 1e-9)
    b = np.maximum(P3.astype(np.float64), 1e-9)
    a = a / a.sum(1, keepdims=True)
    b = b / b.sum(1, keepdims=True)
    S = []
    for w in W:
        s = a ** w * b ** (1.0 - w)
        S.append(s / s.sum(1, keepdims=True))
    ix = np.array([p[0] - 1 for p in PERMS])
    iy = np.array([p[1] - 1 for p in PERMS])
    iz = np.array([p[2] - 1 for p in PERMS])
    s0, s1, s2 = S
    d1 = 1.0 - s1[:, ix]
    d2 = 1.0 - s2[:, ix] - s2[:, iy]
    v = s0[:, ix] * (s1[:, iy] / d1) * (s2[:, iz] / d2)
    v[(d1 <= 0) | (d2 <= 0)] = 0.0
    tot = v.sum(1, keepdims=True)
    return np.where(tot > 0, v / np.maximum(tot, 1e-300), 0.0)


def main() -> None:
    from src.strategy_wt import rank_7t3_blend_probs
    z = C.board()
    PW, P3 = z["PW"], z["P3"]
    idx = list(C.select(None, "all"))[:200]
    V = base_pl(PW[idx], P3[idx])
    cars = list(range(1, 8))
    worst = 0.0
    for j, i in enumerate(idx):
        pw = {c: float(PW[i][c - 1]) for c in cars}
        p3 = {c: float(P3[i][c - 1]) for c in cars}
        ref = rank_7t3_blend_probs(cars, pw, p3, adj_w=(1.0, 1.0))
        for t, p in enumerate(PERMS):
            worst = max(worst, abs(ref.get(p, 0.0) - V[j, t]))
    print(f"max|Δ| vs 本番(adj=1,1) = {worst:.3e}  (n={len(idx)})")
    # 板の PROB との関係も見る
    pb = z["PROB"][idx].astype(np.float64)
    print("板 PROB との max|Δ| =", float(np.abs(pb - V).max()))


if __name__ == "__main__":
    main()
