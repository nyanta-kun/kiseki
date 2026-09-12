#!/usr/bin/env python3
"""順序モデルの学習台 —— 「3車の集合が与えられたとき6並びのどれか」の条件付き選択。

各レースについて **決着の3車集合** の6並びを1グループにし、正解の並びをラベルにする。
オフセットは基礎 PL（ライン隣接ボーナスなし）の log 確率なので、
λ/μ/λr/μr は**この特徴に定数係数を置いた特殊形**になる（＝学習すれば下回らないはず）。

出力 /tmp/oc/ds.npz
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
sys.path.insert(0, str(HERE))

import common as C  # noqa: E402
from plbase import base_pl  # noqa: E402

PERMS = C.CANON
PIDX = {p: t for t, p in enumerate(PERMS)}
OUT = Path("/tmp/oc/ds.npz")

# ── 特徴名 ───────────────────────────────────────────────────────────
PAIRS = (("12", 0, 1), ("23", 1, 2), ("13", 0, 2))
PAIR_KINDS = ("fwd1", "fwd2p", "rev1", "rev2p", "same")

NAMES: list[str] = []
for tag, _, _ in PAIRS:
    for k in PAIR_KINDS:
        NAMES.append(f"{k}_{tag}")
NAMES += ["all3same"]
for pos in ("x", "y", "z"):
    NAMES += [f"lp_{pos}", f"lsize_{pos}", f"leader_{pos}",
              f"nige_{pos}", f"ryo_{pos}", f"solo_{pos}"]
# pw/p3 の差と隣接の交互作用（λr を条件つきにする案の一般形）
NAMES += ["rev1_12_x_dpw", "rev1_12_x_dp3", "fwd1_12_x_dpw", "fwd1_12_x_dp3",
          "rev1_12_x_drp", "fwd1_12_x_drp",
          "rev1_23_x_dpw", "fwd1_23_x_dpw"]
# 市場（予測オッズ）
NAMES += ["mkt"]
NAMES += ["mark_x", "mark_y", "mark_z"]
F = len(NAMES)
IDX = {n: j for j, n in enumerate(NAMES)}

# 腕ごとに使う特徴の集合
SETS = {
    "L1_pair4": [f"{k}_{t}" for t in ("12", "23") for k in ("fwd1", "rev1")],
    "L2_pair15": [f"{k}_{t}" for t, _, _ in PAIRS for k in PAIR_KINDS],
    "L3_struct": ([f"{k}_{t}" for t, _, _ in PAIRS for k in PAIR_KINDS]
                  + ["all3same"]
                  + [f"{a}_{p}" for p in ("x", "y", "z")
                     for a in ("lp", "lsize", "leader", "nige", "ryo", "solo")]),
    "L4_inter": ([f"{k}_{t}" for t, _, _ in PAIRS for k in PAIR_KINDS]
                 + ["all3same"]
                 + [f"{a}_{p}" for p in ("x", "y", "z")
                    for a in ("lp", "lsize", "leader", "nige", "ryo", "solo")]
                 + ["rev1_12_x_dpw", "rev1_12_x_dp3", "fwd1_12_x_dpw",
                    "fwd1_12_x_dp3", "rev1_12_x_drp", "fwd1_12_x_drp",
                    "rev1_23_x_dpw", "fwd1_23_x_dpw"]
                 + ["mark_x", "mark_y", "mark_z"]),
}
SETS["L5_mkt"] = SETS["L4_inter"] + ["mkt"]


def main() -> None:
    z = C.board()
    idx = np.asarray(C.select(None, "all"), dtype=np.int64)
    PW, P3 = z["PW"][idx], z["P3"][idx]
    LG, LP = z["LG"][idx], z["A_line_pos"][idx]
    LS, LD = z["A_line_size"][idx], z["A_is_line_leader"][idx]
    ST, RP = z["ST"][idx], z["A_race_point"][idx]
    MK = z["A_prediction_mark"][idx]
    PO = z["PO"][idx]
    WIN = z["WIN"][idx].astype(np.int64)
    DATE = z["DATE"][idx]
    TYPE = z["TYPE"][idx]
    AX = z["AXIS_SUM"][idx]

    BASE = base_pl(PW, P3)                       # (N,210)
    n = len(idx)
    print(f"races {n:,}")

    # レース内標準化（差の特徴用）
    def zsc(a: np.ndarray) -> np.ndarray:
        m = a.mean(1, keepdims=True)
        s = a.std(1, keepdims=True)
        return (a - m) / np.maximum(s, 1e-9)

    ZPW, ZP3, ZRP = zsc(PW.astype(np.float64)), zsc(P3.astype(np.float64)), zsc(RP.astype(np.float64))

    X = np.zeros((n, 6, F), dtype=np.float32)
    Y = np.zeros(n, dtype=np.int64)
    OFF = np.zeros((n, 6), dtype=np.float64)     # 基礎 PL の log
    PERM = np.zeros((n, 6, 3), dtype=np.int8)
    POW = np.zeros((n, 6), dtype=np.float32)     # 予測オッズ
    ok = np.ones(n, dtype=bool)

    for r in range(n):
        if r % 5000 == 0:
            print(f"  {r:,}/{n:,}", flush=True)
        w = PERMS[WIN[r]]
        s = sorted(w)
        perms = list(itertools.permutations(s))
        lg = {c: str(LG[r][c - 1]) for c in s}
        lp = {c: (int(LP[r][c - 1]) if np.isfinite(LP[r][c - 1]) else 0) for c in s}
        pos = {c: c - 1 for c in s}
        for g, p in enumerate(perms):
            t = PIDX[p]
            OFF[r, g] = np.log(max(float(BASE[r, t]), 1e-12))
            POW[r, g] = PO[r, t]
            PERM[r, g] = p
            v = X[r, g]
            for tag, i0, i1 in PAIRS:
                u, q = p[i0], p[i1]
                same = lg[u] == lg[q]
                d = (lp[q] - lp[u]) if same else 0
                v[IDX[f"same_{tag}"]] = 1.0 if same else 0.0
                v[IDX[f"fwd1_{tag}"]] = 1.0 if (same and d == 1) else 0.0
                v[IDX[f"fwd2p_{tag}"]] = 1.0 if (same and d >= 2) else 0.0
                v[IDX[f"rev1_{tag}"]] = 1.0 if (same and d == -1) else 0.0
                v[IDX[f"rev2p_{tag}"]] = 1.0 if (same and d <= -2) else 0.0
            v[IDX["all3same"]] = 1.0 if (lg[p[0]] == lg[p[1]] == lg[p[2]]) else 0.0
            for nm, c in zip(("x", "y", "z"), p):
                j = pos[c]
                v[IDX[f"lp_{nm}"]] = float(lp[c])
                v[IDX[f"lsize_{nm}"]] = float(LS[r][j])
                v[IDX[f"leader_{nm}"]] = float(LD[r][j])
                v[IDX[f"nige_{nm}"]] = 1.0 if str(ST[r][j]) == "逃" else 0.0
                v[IDX[f"ryo_{nm}"]] = 1.0 if str(ST[r][j]) == "両" else 0.0
                v[IDX[f"solo_{nm}"]] = 1.0 if float(LS[r][j]) <= 1.0 else 0.0
                v[IDX[f"mark_{nm}"]] = float(MK[r][j])
            ix, iy, iz = pos[p[0]], pos[p[1]], pos[p[2]]
            dpw12 = float(ZPW[r, ix] - ZPW[r, iy])
            dp312 = float(ZP3[r, ix] - ZP3[r, iy])
            drp12 = float(ZRP[r, ix] - ZRP[r, iy])
            dpw23 = float(ZPW[r, iy] - ZPW[r, iz])
            v[IDX["rev1_12_x_dpw"]] = v[IDX["rev1_12"]] * dpw12
            v[IDX["rev1_12_x_dp3"]] = v[IDX["rev1_12"]] * dp312
            v[IDX["rev1_12_x_drp"]] = v[IDX["rev1_12"]] * drp12
            v[IDX["fwd1_12_x_dpw"]] = v[IDX["fwd1_12"]] * dpw12
            v[IDX["fwd1_12_x_dp3"]] = v[IDX["fwd1_12"]] * dp312
            v[IDX["fwd1_12_x_drp"]] = v[IDX["fwd1_12"]] * drp12
            v[IDX["rev1_23_x_dpw"]] = v[IDX["rev1_23"]] * dpw23
            v[IDX["fwd1_23_x_dpw"]] = v[IDX["fwd1_23"]] * dpw23
            if p == w:
                Y[r] = g
        po6 = POW[r]
        if np.all(np.isfinite(po6)) and np.all(po6 > 0):
            m = -np.log(po6.astype(np.float64))
            X[r, :, IDX["mkt"]] = (m - m.mean()).astype(np.float32)
        else:
            ok[r] = False

    np.savez_compressed(
        OUT, X=X, Y=Y, OFF=OFF, PERM=PERM, PO=POW, ok=ok,
        NAMES=np.array(NAMES), I=idx, DATE=DATE, TYPE=TYPE, AXIS=AX)
    print(f"→ {OUT}  F={F}  mkt欠測 {int((~ok).sum()):,}")


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    main()
