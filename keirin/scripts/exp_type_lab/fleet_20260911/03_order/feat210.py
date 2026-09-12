#!/usr/bin/env python3
"""1レースの 210順列ぶんの特徴を一度に作る（`build_ds.py` と同じ定義）。

`build_ds.py` は決着の集合の6並びだけを作る（学習用）。買い目へ当てるには
**買った点の並びすべて**を評価する必要があるので、210 本まとめて作る版を用意する。
定義が食い違うと学習した係数を別の特徴に掛けることになるので、
`--verify` で `build_ds.py` の出力と突き合わせる。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
sys.path.insert(0, str(HERE))

import common as C  # noqa: E402
from build_ds import IDX, NAMES, PAIRS, F  # noqa: E402

PERMS = C.CANON
IX = np.array([p[0] - 1 for p in PERMS])
IY = np.array([p[1] - 1 for p in PERMS])
IZ = np.array([p[2] - 1 for p in PERMS])
POSI = {"x": IX, "y": IY, "z": IZ}

_g: dict = {}
for _t, _p in enumerate(PERMS):
    _g.setdefault(tuple(sorted(_p)), []).append(_t)
_SETGROUPS = [np.array(v) for v in _g.values()]


def feat210(lg: np.ndarray, lp: np.ndarray, ls: np.ndarray, ld: np.ndarray,
            st: np.ndarray, mk: np.ndarray, zpw: np.ndarray, zp3: np.ndarray,
            zrp: np.ndarray, po: np.ndarray) -> np.ndarray:
    """(210, F) の特徴行列。引数はいずれも長さ7の配列（車番 1..7 の順）。"""
    v = np.zeros((210, F), dtype=np.float64)
    lgi = np.asarray([str(x) for x in lg])
    lpi = np.asarray([int(x) if np.isfinite(x) else 0 for x in lp], dtype=np.int64)
    for tag, i0, i1 in PAIRS:
        a = POSI["xyz"[i0]]
        b = POSI["xyz"[i1]]
        same = lgi[a] == lgi[b]
        d = np.where(same, lpi[b] - lpi[a], 0)
        v[:, IDX[f"same_{tag}"]] = same
        v[:, IDX[f"fwd1_{tag}"]] = same & (d == 1)
        v[:, IDX[f"fwd2p_{tag}"]] = same & (d >= 2)
        v[:, IDX[f"rev1_{tag}"]] = same & (d == -1)
        v[:, IDX[f"rev2p_{tag}"]] = same & (d <= -2)
    v[:, IDX["all3same"]] = (lgi[IX] == lgi[IY]) & (lgi[IY] == lgi[IZ])
    sts = np.asarray([str(x) for x in st])
    for nm in ("x", "y", "z"):
        j = POSI[nm]
        v[:, IDX[f"lp_{nm}"]] = lpi[j]
        v[:, IDX[f"lsize_{nm}"]] = ls[j]
        v[:, IDX[f"leader_{nm}"]] = ld[j]
        v[:, IDX[f"nige_{nm}"]] = (sts[j] == "逃")
        v[:, IDX[f"ryo_{nm}"]] = (sts[j] == "両")
        v[:, IDX[f"solo_{nm}"]] = (ls[j] <= 1.0)
        v[:, IDX[f"mark_{nm}"]] = mk[j]
    dpw12 = zpw[IX] - zpw[IY]
    dp312 = zp3[IX] - zp3[IY]
    drp12 = zrp[IX] - zrp[IY]
    dpw23 = zpw[IY] - zpw[IZ]
    v[:, IDX["rev1_12_x_dpw"]] = v[:, IDX["rev1_12"]] * dpw12
    v[:, IDX["rev1_12_x_dp3"]] = v[:, IDX["rev1_12"]] * dp312
    v[:, IDX["rev1_12_x_drp"]] = v[:, IDX["rev1_12"]] * drp12
    v[:, IDX["fwd1_12_x_dpw"]] = v[:, IDX["fwd1_12"]] * dpw12
    v[:, IDX["fwd1_12_x_dp3"]] = v[:, IDX["fwd1_12"]] * dp312
    v[:, IDX["fwd1_12_x_drp"]] = v[:, IDX["fwd1_12"]] * drp12
    v[:, IDX["rev1_23_x_dpw"]] = v[:, IDX["rev1_23"]] * dpw23
    v[:, IDX["fwd1_23_x_dpw"]] = v[:, IDX["fwd1_23"]] * dpw23
    # mkt は「同じ集合の6並びの中で中心化」なので集合ごとに引く
    m = -np.log(np.maximum(po, 1e-9))
    for ts in _SETGROUPS:
        v[ts, IDX["mkt"]] = m[ts] - m[ts].mean()
    return v


def verify() -> None:
    D = np.load("/tmp/oc/ds.npz", allow_pickle=True)
    X, PERM, I = D["X"], D["PERM"], D["I"]
    z = C.board()
    PIDX = {p: t for t, p in enumerate(PERMS)}
    worst = 0.0
    rng = np.random.default_rng(3)
    sel = rng.choice(len(I), 300, replace=False)

    def zsc(a):
        return (a - a.mean()) / max(a.std(), 1e-9)
    for r in sel:
        i = int(I[r])
        v = feat210(z["LG"][i], z["A_line_pos"][i], z["A_line_size"][i],
                    z["A_is_line_leader"][i], z["ST"][i],
                    z["A_prediction_mark"][i],
                    zsc(z["PW"][i].astype(np.float64)),
                    zsc(z["P3"][i].astype(np.float64)),
                    zsc(z["A_race_point"][i].astype(np.float64)),
                    z["PO"][i].astype(np.float64))
        for g in range(6):
            t = PIDX[tuple(int(c) for c in PERM[r, g])]
            worst = max(worst, float(np.abs(v[t] - X[r, g]).max()))
    print(f"feat210 vs build_ds  max|Δ| = {worst:.3e}  (n=300レース)")


if __name__ == "__main__":
    verify()
