"""H01 共通: 2025 の7車台（vintage 予測オッズに差し替え済み）を作る。"""
from __future__ import annotations
import itertools, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts/exp_type_lab"))

PERMS = list(itertools.permutations(range(1, 8), 3))
PIDX = {p: i for i, p in enumerate(PERMS)}
D = REPO / "data/exp_bet_review"
Y0, Y1 = "2025-01-01", "2025-12-31"
NEED = ("PO", "WIN", "PAY", "KEY", "DATE", "P3", "PW", "LG", "ST", "A_line_pos",
        "A_race_point", "BEHIND", "DAYI", "TRIO_PO", "TRIO_WIN", "TRIO_PAY",
        "TYPE", "AGREE", "AXIS_SUM", "GAP", "RTYPE", "CUPG", "OKPRED", "GRADE")


def vintage_po(keys):
    """keys に対する vintage 予測オッズ (N,210)。無いレースは NaN 行。"""
    v = np.load(D / "odds_tf_vintage2024_2025.npz", allow_pickle=True)
    m = v["n_car"] == 7
    rk, comb, po = v["race_key"][m], v["combination"][m], v["pred_odds"][m]
    kidx = {str(k): i for i, k in enumerate(keys)}
    out = np.full((len(keys), 210), np.nan)
    for r, c, o in zip(rk, comb, po):
        i = kidx.get(str(r))
        if i is None:
            continue
        t = tuple(int(x) for x in c.split("-"))
        out[i, PIDX[t]] = o
    return out


def load_board_2025():
    """台（2025・7車）を dict で返す。PO は vintage に差し替える。vintage の無い行は OKPRED=False。"""
    z = np.load(D / "race_type_board.npz", allow_pickle=True)
    date = z["DATE"]
    m = (date >= Y0) & (date <= Y1)
    b = {k: z[k][m] for k in NEED}
    b["PO_PROD"] = b["PO"].copy()
    vp = vintage_po(b["KEY"])
    ok = np.isfinite(vp).all(1)
    b["PO"] = np.where(ok[:, None], vp, np.nan).astype(np.float64)
    b["OKPRED"] = b["OKPRED"] & ok
    b["HAS_VINTAGE"] = ok
    return b
