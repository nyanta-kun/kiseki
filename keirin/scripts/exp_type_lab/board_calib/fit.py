#!/usr/bin/env python3
"""組番確率の較正（事前登録 docs/type_lab/board_calibration_2026_10_28.md §3・§4・§6-1）。

p*_k ∝ exp(a_g·log p_k + b_g·log q_k)。p = 本番の PL 板、q = 予測オッズの含意確率。
学習 2025（探索用 vintage 予測オッズ）/ 確認 2026-01〜08-04（本番 odds_tf・学習外）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/board_calib/fit.py

入力（git 管理外）: data/exp_bet_review/race_type_board.npz / h06_pop.pkl
出力: data/exp_bet_review/board_calib_feats.npz・board_calib_coef.json と stdout
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from src.strategy_wt import rank_7t3_blend_probs  # noqa: E402

D = REPO / "data" / "exp_bet_review"
import itertools  # noqa: E402

PERMS = list(itertools.permutations(range(1, 8), 3))
FLOOR = 1e-9
N_BOOT = 2000


def is_semi(rtype: str) -> bool:
    """事前登録 §3 の群: RTYPE に「準決勝」を含む。"""
    return "準決勝" in rtype


def race_p(z, i: int) -> np.ndarray:
    """本番の PL 板（lineup_sim.ctx と同じ引数）を PERMS 順に並べる。"""
    cars = list(range(1, 8))
    p3 = {c: float(z["P3"][i][c - 1]) for c in cars}
    pw = {c: float(z["PW"][i][c - 1]) for c in cars}
    lg = {c: str(z["LG"][i][c - 1]) for c in cars}
    lp = {c: float(z["A_line_pos"][i][c - 1]) for c in cars}
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    return np.array([pr.get(k, 0.0) for k in PERMS])


def q_from_po(po: np.ndarray) -> np.ndarray:
    inv = np.where(np.isfinite(po) & (po > 0), 1.0 / po, 0.0)
    return inv / inv.sum()


def build():
    z0 = np.load(D / "race_type_board.npz", allow_pickle=True)
    z = {k: z0[k] for k in ("P3", "PW", "LG", "A_line_pos", "PO", "WIN", "PAY", "KEY",
                            "DATE", "RTYPE", "TYPE", "OKPRED", "TRIO_WIN", "TRIO_PAY")}
    pop = pickle.load(open(D / "h06_pop.pkl", "rb"))
    V = pop["V"]
    rows = []
    for i in pop["pop"]:                      # 2025・vintage 予測オッズ 210点そろい
        rows.append((int(i), "fit", V[int(i)].astype(float)))
    D_ = z["DATE"]
    m26 = ((D_ >= "2026-01-01") & (z["WIN"] >= 0) & np.isfinite(z["PAY"]) & z["OKPRED"]
           & (z["TYPE"] != ""))
    for i in np.flatnonzero(m26):
        po = z["PO"][i].astype(float)
        if np.isfinite(po).sum() == 210:
            rows.append((int(i), "conf", po))
    n = len(rows)
    LP = np.empty((n, 210)); LQ = np.empty((n, 210))
    WIN = np.empty(n, int); G = np.empty(n, bool); SPLIT = np.empty(n, "U4")
    IDX = np.empty(n, int); DAY = np.empty(n, "U10"); PO = np.empty((n, 210))
    for r, (i, sp, po) in enumerate(rows):
        p = race_p(z, i)
        LP[r] = np.log(np.maximum(p, FLOOR))
        LQ[r] = np.log(np.maximum(q_from_po(po), FLOOR))
        WIN[r] = int(z["WIN"][i]); G[r] = is_semi(str(z["RTYPE"][i]))
        SPLIT[r] = sp; IDX[r] = i; DAY[r] = str(z["DATE"][i]); PO[r] = po
    np.savez_compressed(D / "board_calib_feats.npz", LP=LP, LQ=LQ, WIN=WIN, G=G,
                        SPLIT=SPLIT, IDX=IDX, DAY=DAY, PO=PO)
    return dict(LP=LP, LQ=LQ, WIN=WIN, G=G, SPLIT=SPLIT, IDX=IDX, DAY=DAY, PO=PO)


def logp_star(LP, LQ, a, b):
    s = a * LP + b * LQ
    s = s - s.max(1, keepdims=True)
    return s - np.log(np.exp(s).sum(1, keepdims=True))


def nll_rows(LP, LQ, WIN, a, b):
    lps = logp_star(LP, LQ, a, b)
    return -lps[np.arange(len(WIN)), WIN]


def fit_ab(LP, LQ, WIN):
    f = lambda t: nll_rows(LP, LQ, WIN, t[0], t[1]).mean()  # noqa: E731
    r = minimize(f, x0=[1.0, 0.0], method="Nelder-Mead",
                 options=dict(xatol=1e-5, fatol=1e-8, maxiter=2000))
    return float(r.x[0]), float(r.x[1]), float(r.fun)


def main():
    f = D / "board_calib_feats.npz"
    if f.exists() and "--rebuild" not in sys.argv:
        d = dict(np.load(f))
    else:
        d = build()
    LP, LQ, WIN, G, SPLIT = d["LP"], d["LQ"], d["WIN"], d["G"], d["SPLIT"]
    fit = SPLIT == "fit"; conf = SPLIT == "conf"
    print(f"学習 {fit.sum()}R（準決勝 {(fit & G).sum()}） / 確認 {conf.sum()}R（準決勝 {(conf & G).sum()}）")
    # 目視: 確認窓の最初の1レース
    r0 = int(np.flatnonzero(conf)[0])
    top = np.argsort(-LP[r0])[:5]
    print("目視 idx", int(d["IDX"][r0]), d["DAY"][r0], "勝ち目", PERMS[WIN[r0]],
          "p上位", [(PERMS[k], round(float(np.exp(LP[r0, k])), 4), round(float(np.exp(LQ[r0, k])), 4)) for k in top])

    coef = {}
    a1, b1, f1 = fit_ab(LP[fit], LQ[fit], WIN[fit])
    coef["M1"] = dict(a=a1, b=b1)
    for gname, gm in (("semi", G), ("other", ~G)):
        a, b, _ = fit_ab(LP[fit & gm], LQ[fit & gm], WIN[fit & gm])
        coef[f"M2_{gname}"] = dict(a=a, b=b)
    print("係数", json.dumps(coef, ensure_ascii=False))
    json.dump(coef, open(D / "board_calib_coef.json", "w"), ensure_ascii=False, indent=1)

    def per_race_nll(mask, model):
        if model == "M0":
            return nll_rows(LP[mask], LQ[mask], WIN[mask], 1.0, 0.0)
        if model == "MQ":
            return nll_rows(LP[mask], LQ[mask], WIN[mask], 0.0, 1.0)
        if model == "M1":
            return nll_rows(LP[mask], LQ[mask], WIN[mask], coef["M1"]["a"], coef["M1"]["b"])
        out = np.empty(mask.sum())
        g = G[mask]
        for gname, gm in (("semi", g), ("other", ~g)):
            c = coef[f"M2_{gname}"]
            out[gm] = nll_rows(LP[mask][gm], LQ[mask][gm], WIN[mask][gm], c["a"], c["b"])
        return out

    rng = np.random.default_rng(20261028)
    for wname, wm in (("学習2025", fit), ("確認2026", conf)):
        for sub, sm in (("全体", np.ones_like(G)), ("準決勝", G), ("それ以外", ~G)):
            m = wm & sm
            base = per_race_nll(m, "M0")
            line = f"{wname} {sub:<5} n={m.sum():>6}  M0 {base.mean():.4f}"
            for mod in ("MQ", "M1", "M2"):
                v = per_race_nll(m, mod)
                diff = v - base
                bs = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(N_BOOT)]
                line += f" | {mod} {v.mean():.4f} Δ{diff.mean():+.4f} [{np.percentile(bs, 2.5):+.4f},{np.percentile(bs, 97.5):+.4f}]"
            print(line, flush=True)

    # 確認窓の信頼度表: p×予測オッズ（本番 PO）帯ごとの 実際/Σp（全 210 目）
    m = conf
    PO = d["PO"][m]; win = WIN[m]; g = G[m]
    hit = np.zeros_like(PO, bool); hit[np.arange(len(win)), win] = True
    for mod in ("M0", "M2"):
        if mod == "M0":
            P = np.exp(LP[m])
        else:
            P = np.empty_like(PO)
            for gname, gm in (("semi", g), ("other", ~g)):
                c = coef[f"M2_{gname}"]
                P[gm] = np.exp(logp_star(LP[m][gm], LQ[m][gm], c["a"], c["b"]))
        ev = P * PO
        print(f"確認2026 信頼度 {mod}（p×予測オッズ帯: 実際/Σp・準決勝以外 | 準決勝）")
        for lo, hi in ((0, .5), (.5, .8), (.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 2), (2, 1e9)):
            cell = (ev >= lo) & (ev < hi)
            out = []
            for gm in (~g, g):
                cm = cell & gm[:, None]
                out.append(f"{hit[cm].sum() / P[cm].sum():.2f} (n={cm.sum()})")
            print(f"   {lo:>4}-{hi:<6} " + " | ".join(out))


if __name__ == "__main__":
    main()
