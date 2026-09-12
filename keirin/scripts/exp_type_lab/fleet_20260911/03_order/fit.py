#!/usr/bin/env python3
"""順序モデルの学習と天井の測定（vintage・2 fold）。

fold A: train <= 2025-06-30  /  eval 2025-07-01〜2025-12-31（探索窓の後半・OOS）
fold B: train <= 2025-12-31  /  eval 2026-01-01〜（確認窓・本番相当）

腕:
  base        基礎 PL のみ（ボーナスなし）
  prod_fwd    λ=2.0 / μ=1.5（2026-09-10 までの本番）
  prod_rev    + λr=1.9（2026-09-11 の本番・PR#567）
  mkt         予測オッズ最安順（市場）
  L1..L5      条件付きロジット（基礎 PL を offset に、特徴を学習）
  lgb         LightGBM lambdarank（group=6・offset を特徴として渡す）
  oracle/uniform
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_ds import SETS  # noqa: E402

D = np.load("/tmp/oc/ds.npz", allow_pickle=True)
X = D["X"].astype(np.float64)
Y = D["Y"]
OFF = D["OFF"]
PERM = D["PERM"]
PO = D["PO"].astype(np.float64)
NAMES = [str(v) for v in D["NAMES"]]
DATE = np.array([str(v) for v in D["DATE"]])
TYPE = np.array([str(v) for v in D["TYPE"]])
IDX = {n: j for j, n in enumerate(NAMES)}
N = len(Y)

FOLDS = [
    ("A", DATE <= "2025-06-30", (DATE >= "2025-07-01") & (DATE <= "2025-12-31")),
    ("B", DATE <= "2025-12-31", DATE >= "2026-01-01"),
]


def softmax(s: np.ndarray) -> np.ndarray:
    s = s - s.max(1, keepdims=True)
    e = np.exp(s)
    return e / e.sum(1, keepdims=True)


def nll(beta, Xs, off, y, l2):
    s = off + Xs @ beta
    s = s - s.max(1, keepdims=True)
    lse = np.log(np.exp(s).sum(1))
    ll = s[np.arange(len(y)), y] - lse
    p = softmax(off + Xs @ beta)
    g = p.copy()
    g[np.arange(len(y)), y] -= 1.0
    grad = np.einsum("rg,rgf->f", g, Xs) / len(y) + 2 * l2 * beta
    return -ll.mean() + l2 * float(beta @ beta), grad


def fit_cl(cols, tr, l2=1e-4):
    js = [IDX[c] for c in cols]
    Xs = X[tr][:, :, js]
    res = minimize(nll, np.zeros(len(js)), args=(Xs, OFF[tr], Y[tr], l2),
                   jac=True, method="L-BFGS-B",
                   options=dict(maxiter=500, ftol=1e-12, gtol=1e-10))
    return js, res.x


def score_of(arm, params, m):
    """(n,6) のスコア（大きいほど買う側）。"""
    if arm == "base":
        return OFF[m]
    if arm == "prod_fwd":
        return (OFF[m] + math.log(2.0) * X[m][:, :, IDX["fwd1_12"]]
                + math.log(1.5) * X[m][:, :, IDX["fwd1_23"]])
    if arm == "prod_rev":
        return (OFF[m] + math.log(2.0) * X[m][:, :, IDX["fwd1_12"]]
                + math.log(1.5) * X[m][:, :, IDX["fwd1_23"]]
                + math.log(1.9) * X[m][:, :, IDX["rev1_12"]])
    if arm == "mkt":
        return -np.log(np.maximum(PO[m], 1e-9))
    if arm == "uniform":
        # 🔴 零行列の argmax は常に index 0（＝車番昇順の並び）を返してしまう。
        #    無作為に崩して本当の 1/6 にする。
        return np.random.default_rng(0).random((int(m.sum()), 6))
    js, beta = params
    return OFF[m] + X[m][:, :, js] @ beta


def top1(s, y):
    return float((s.argmax(1) == y).mean())


def ll_of(s, y):
    p = softmax(s)
    return float(-np.log(np.maximum(p[np.arange(len(y)), y], 1e-15)).mean())


def boot_diff(sa, sb, y, n=2000, seed=7):
    """paired bootstrap（レース単位）で top1 の差の 95%CI。"""
    ha = (sa.argmax(1) == y).astype(float)
    hb = (sb.argmax(1) == y).astype(float)
    d = hb - ha
    rng = np.random.default_rng(seed)
    m = len(d)
    out = np.empty(n)
    for i in range(n):
        out[i] = d[rng.integers(0, m, m)].mean()
    return d.mean() * 100, float(np.percentile(out, 2.5)) * 100, float(np.percentile(out, 97.5)) * 100


def main() -> None:
    import lightgbm as lgb
    import pickle
    dump: dict = {}
    arms = ["base", "prod_fwd", "prod_rev", "mkt", "uniform"]
    learned = list(SETS)
    for tag, tr, ev in FOLDS:
        print(f"\n{'='*78}\nfold {tag}: train n={int(tr.sum()):,} ({sorted(DATE[tr])[0]}〜{sorted(DATE[tr])[-1]})"
              f"  eval n={int(ev.sum()):,} ({sorted(DATE[ev])[0]}〜{sorted(DATE[ev])[-1]})")
        params = {}
        for nm in learned:
            params[nm] = fit_cl(SETS[nm], tr)
            dump[(tag, nm)] = {c: float(b)
                               for c, b in zip(SETS[nm], params[nm][1])}
        # LightGBM lambdarank
        js_all = [IDX[c] for c in SETS["L5_mkt"]]
        def flat(m):
            xs = np.concatenate([OFF[m].reshape(-1, 1),
                                X[m][:, :, js_all].reshape(-1, len(js_all))], 1)
            lab = np.zeros(int(m.sum()) * 6)
            lab[np.arange(int(m.sum())) * 6 + Y[m]] = 1
            return xs, lab
        xtr, ltr = flat(tr)
        ds = lgb.Dataset(xtr, label=ltr, group=[6] * int(tr.sum()))
        bst = lgb.train(dict(objective="lambdarank", metric="ndcg", ndcg_eval_at=[1],
                             learning_rate=0.05, num_leaves=31, min_data_in_leaf=200,
                             feature_fraction=0.9, bagging_fraction=0.8,
                             bagging_freq=1, verbose=-1, seed=1),
                        ds, num_boost_round=300)
        xev, _ = flat(ev)
        s_lgb = bst.predict(xev).reshape(-1, 6)

        y = Y[ev]
        S = {a: score_of(a, None, ev) for a in arms}
        for nm in learned:
            S[nm] = score_of(nm, params[nm], ev)
        S["lgb"] = s_lgb
        base_ref = S["prod_rev"]
        print(f"{'腕':<12} {'top1(順序一致)':>14} {'logloss':>9} {'Δ vs prod_rev(pt)':>26}")
        for a in ["uniform", "base", "prod_fwd", "prod_rev", "mkt"] + learned + ["lgb"]:
            s = S[a]
            d, lo, hi = boot_diff(base_ref, s, y)
            lls = "—" if a in ("mkt", "lgb", "uniform") else f"{ll_of(s, y):9.4f}"
            print(f"{a:<12} {top1(s, y)*100:13.2f}% {lls:>9} "
                  f"{d:+8.2f} [{lo:+.2f},{hi:+.2f}]")
        print(f"{'oracle':<12} {100.0:13.2f}%")

        # ── 決着の並びの形で分解（どの形で勝っているか）─────────────
        def kind(r: int) -> str:
            v = X[r, Y[r]]
            if v[IDX["all3same"]]:
                return "同ライン3車"
            if v[IDX["fwd1_12"]]:
                return "1-2同ライン 順(先頭→番手)"
            if v[IDX["rev1_12"]]:
                return "1-2同ライン 逆(番手→先頭)"
            if v[IDX["same_12"]]:
                return "1-2同ライン 非隣接"
            return "1-2別ライン"
        ev_i = np.flatnonzero(ev)
        kd = np.array([kind(r) for r in ev_i])
        print(f"\n{'決着の形':<26} {'n':>6} {'割合':>7} {'prod_rev':>9} "
              f"{'L4_inter':>9} {'mkt':>7} {'Δ(L4-rev)':>10}")
        for k in ["同ライン3車", "1-2同ライン 順(先頭→番手)", "1-2同ライン 逆(番手→先頭)",
                  "1-2同ライン 非隣接", "1-2別ライン"]:
            m2 = kd == k
            if not m2.any():
                continue
            a = (S["prod_rev"].argmax(1) == y)[m2].mean()
            b = (S["L4_inter"].argmax(1) == y)[m2].mean()
            c2 = (S["mkt"].argmax(1) == y)[m2].mean()
            print(f"{k:<26} {int(m2.sum()):6,} {m2.mean()*100:6.1f}% "
                  f"{a*100:8.2f}% {b*100:8.2f}% {c2*100:6.2f}% {(b-a)*100:+9.2f}")

        # 係数（L2_pair15 と L5_mkt）
        for nm in ("L1_pair4", "L2_pair15", "L5_mkt"):
            js, beta = params[nm]
            print(f"\n-- {nm} の係数（exp = 乗数）")
            for c, b in sorted(zip(SETS[nm], beta), key=lambda t: -abs(t[1])):
                if abs(b) < 0.02:
                    continue
                print(f"   {c:<18} β={b:+.4f}  exp={math.exp(b):.3f}")
    pickle.dump(dump, open("/tmp/oc/beta.pkl", "wb"))
    print("\n→ /tmp/oc/beta.pkl")


if __name__ == "__main__":
    main()
