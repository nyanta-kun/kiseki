#!/usr/bin/env python3
"""目的関数の A/B — 「1車が3着以内か」の二値 → **レース内の順序（Plackett-Luce）**（2026-09-14）。

## なぜこの腕か

本番の学習は `lgbm_wt`(top3_flag / binary) と `lgbm_wt_win`(win_flag / binary) の
**独立した2つの二値分類**（選手1行ずつ）。一方**消費**は

  - `type_lab.race_shape` の `order` = p3 降順 / `axis_sum` = 上位2車の p3 の和
  - `strategy_wt.rank_7t3_blend_probs` = pw と p3 を**位置別に合成した Plackett-Luce**
    で三連単の同時確率を組む

＝ **消費側は既に PL を仮定している**のに、その強度を「独立に学習した2本の二値確率」で
代用している。ならば **PL の尤度そのものを最大化して強度を学習する**のが最も近い。
1本のモデルから pw（＝softmax）と p3（＝PL の上位3入着確率）が**整合して**出る。

目的関数（ListMLE・上位K段）:

    L = Σ_{k=1..K} [ -s_{π(k)} + log Σ_{j ∈ R_k} exp(s_j) ]

`R_k` は k 段目の残存集合（落車・失格・欠場は**分母には残す**が分子には現れない）。
K=3 は三連単そのもの、K=6 は下位の順序も使う版。

比較のため **レース内正規化着順の回帰**（中央競馬 v27 の `reg_rank` と同型）も置く。

⚠️ 窓 w1/w2 は `keirin_protocol.BURNED_WINDOWS` の焼けた窓＝**VAL**。
   出るのは探索の結果で採否ではない。

    PYTHONPATH=. .venv/bin/python scripts/exp_objective_pl_ab.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import FEATURE_COLS_WT, TARGET_COL_WT  # noqa: E402

FEAT_PKL = Path("/tmp/plab/feat.pkl")
TRAIN_FROM = "2024-04-01"
WINDOWS = {"w1": ("2026-04-13", "2026-07-15"), "w2": ("2026-01-01", "2026-04-12")}
SEEDS = [42, 101, 202, 303, 404]
PARAMS = dict(num_leaves=31, learning_rate=0.05, min_child_samples=20,
              feature_fraction=0.8, verbose=-1, num_threads=8,
              deterministic=True, force_row_wise=True)
N_ROUNDS = 500
MAXC = 9


# --------------------------------------------------------------------------- 台
def race_layout(df: pd.DataFrame) -> dict:
    """レース単位の行列レイアウト（行 → (レース, スロット)）を作る。"""
    rk = df["race_key"].values
    _, race_idx = np.unique(rk, return_inverse=True)
    m = int(race_idx.max()) + 1
    # レース内の出現順をスロットに
    slot = np.zeros(len(df), dtype=np.int64)
    cnt = np.zeros(m, dtype=np.int64)
    for i, r in enumerate(race_idx):
        slot[i] = cnt[r]
        cnt[r] += 1
    ok = slot < MAXC
    valid = np.zeros((m, MAXC), dtype=bool)
    valid[race_idx[ok], slot[ok]] = True
    fo = pd.to_numeric(df["finish_order"], errors="coerce").fillna(0).values
    order_mat = np.full((m, MAXC), 0.0)
    order_mat[race_idx[ok], slot[ok]] = fo[ok]
    return dict(race_idx=race_idx, slot=slot, m=m, valid=valid, order=order_mat,
                row_ok=ok, n_rows=len(df))


def stage_slots(lay: dict, k_max: int) -> tuple[np.ndarray, np.ndarray]:
    """各レースの k 段目に入る車のスロット（-1 = その段なし）と使用可否。"""
    m = lay["m"]
    order = lay["order"]
    st = np.full((m, k_max), -1, dtype=np.int64)
    for k in range(k_max):
        pos = k + 1
        hit = (order == pos)
        cnt = hit.sum(1)
        idx = np.argmax(hit, axis=1)
        st[:, k] = np.where(cnt == 1, idx, -1)
    # 先頭から連続して埋まっている段だけ使う
    usable = st[:, 0] >= 0
    for k in range(1, k_max):
        st[:, k] = np.where(usable & (st[:, k] >= 0), st[:, k], -1)
        usable = usable & (st[:, k] >= 0)
    return st, st[:, 0] >= 0


class PLObjective:
    """ListMLE（上位K段の Plackett-Luce 負対数尤度）。"""

    def __init__(self, lay: dict, k_max: int):
        self.lay = lay
        self.k = k_max
        self.st, self.ok = stage_slots(lay, k_max)
        self.rows = np.arange(lay["m"])
        # 段ごとに「その段が存在するレース」
        self.has = self.st >= 0

    def __call__(self, y_pred, _dataset=None):
        lay = self.lay
        m = lay["m"]
        S = np.full((m, MAXC), -np.inf)
        S[lay["race_idx"][lay["row_ok"]], lay["slot"][lay["row_ok"]]] = y_pred[lay["row_ok"]]
        g = np.zeros((m, MAXC))
        h = np.zeros((m, MAXC))
        removed = np.zeros((m, MAXC), dtype=bool)
        for k in range(self.k):
            live = self.has[:, k]
            logits = np.where(removed, -np.inf, S)
            mx = np.max(np.where(np.isfinite(logits), logits, -np.inf), axis=1, keepdims=True)
            mx = np.where(np.isfinite(mx), mx, 0.0)
            e = np.exp(np.clip(logits - mx, -60, 60))
            e[~np.isfinite(logits)] = 0.0
            tot = e.sum(1, keepdims=True)
            p = np.where(tot > 0, e / np.maximum(tot, 1e-300), 0.0)
            p[~live] = 0.0
            g += p
            h += p * (1.0 - p)
            sel = self.st[:, k]
            rr = self.rows[live]
            g[rr, sel[live]] -= 1.0
            removed[rr, sel[live]] = True
        grad = np.zeros(lay["n_rows"])
        hess = np.full(lay["n_rows"], 1e-6)
        ri, si, ro = lay["race_idx"], lay["slot"], lay["row_ok"]
        grad[ro] = g[ri[ro], si[ro]]
        hess[ro] = np.maximum(h[ri[ro], si[ro]], 1e-6)
        return grad, hess


# --------------------------------------------------- PL スコア → pw / p3
def pl_probs(lay: dict, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """PL の raw score から (pw, p3) を行単位で返す。"""
    m = lay["m"]
    ri, si, ro = lay["race_idx"], lay["slot"], lay["row_ok"]
    S = np.full((m, MAXC), -np.inf)
    S[ri[ro], si[ro]] = score[ro]
    mx = np.max(np.where(np.isfinite(S), S, -np.inf), axis=1, keepdims=True)
    e = np.exp(np.clip(S - mx, -60, 60))
    e[~np.isfinite(S)] = 0.0
    s = e / np.maximum(e.sum(1, keepdims=True), 1e-300)     # (m, C) 強度・和1
    s = np.clip(s, 0.0, 1.0 - 1e-9)
    one_minus = np.maximum(1.0 - s, 1e-9)
    u = (s / one_minus).sum(1, keepdims=True)               # Σ_x s_x/(1-s_x)
    p2 = s * (u - s / one_minus)
    # P3: W_xy = s_x*s_y/(1-s_x), D_xy = 1-s_x-s_y
    sx = s[:, :, None]
    sy = s[:, None, :]
    W = sx * sy / one_minus[:, :, None]
    D = np.maximum(1.0 - sx - sy, 1e-9)
    R = W / D
    eye = np.eye(MAXC, dtype=bool)
    R[:, eye] = 0.0
    T = R.sum((1, 2))[:, None]
    p3v = s * (T - R.sum(2) - R.sum(1))
    p3 = np.clip(s + p2 + p3v, 1e-9, 1.0)
    return s[ri, si], p3[ri, si]


# --------------------------------------------------------------------- 指標
def race_metrics(df: pd.DataFrame, prob: np.ndarray, n7: np.ndarray) -> tuple:
    t = pd.DataFrame({"race_key": df["race_key"].values,
                      "fo": pd.to_numeric(df["finish_order"], errors="coerce").fillna(99).values,
                      "p": prob, "n7": n7})
    t = t[t["n7"]]
    win = top3 = pair = n = 0
    for _rk, g in t.groupby("race_key", sort=False):
        if len(g) != 7:
            continue
        if (g["fo"].between(1, 3)).sum() < 3:
            continue
        o = g.sort_values("p", ascending=False)
        f1 = o["fo"].iloc[0]
        f2 = o["fo"].iloc[1]
        n += 1
        win += 1 if f1 == 1 else 0
        top3 += 1 if 1 <= f1 <= 3 else 0
        pair += 1 if (1 <= f1 <= 3 and 1 <= f2 <= 3) else 0
    return (win / n, top3 / n, pair / n, n) if n else (0, 0, 0, 0)


def axis_sum_stats(df: pd.DataFrame, p3: np.ndarray, n7: np.ndarray) -> tuple:
    t = pd.DataFrame({"race_key": df["race_key"].values, "p": p3, "n7": n7})
    t = t[t["n7"]]
    vals = []
    for _rk, g in t.groupby("race_key", sort=False):
        if len(g) != 7:
            continue
        v = np.sort(g["p"].values)[::-1]
        vals.append(v[0] + v[1])
    a = np.array(vals)
    return float(a.mean()), float((a >= 1.44).mean())


# ----------------------------------------------------------------------- 本体
def main() -> None:
    from sklearn.metrics import roc_auc_score

    df = pd.read_pickle(FEAT_PKL)
    df = df[df["finish_order"].notna()].copy()
    print(f"読み込み {len(df):,}行 / {df['race_key'].nunique():,}レース", flush=True)
    cols = list(FEATURE_COLS_WT)
    df["win_flag"] = (pd.to_numeric(df["finish_order"], errors="coerce") == 1).astype(int)

    for wn, (tf, tt) in WINDOWS.items():
        tr = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < tf)].reset_index(drop=True)
        te = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)].reset_index(drop=True)
        lay_tr, lay_te = race_layout(tr), race_layout(te)
        n7_te = te.groupby("race_key")["race_key"].transform("size").values == 7
        ytr3 = tr[TARGET_COL_WT].values
        yte3 = te[TARGET_COL_WT].values
        yte1 = te["win_flag"].values
        # 正規化着順ラベル（中央 v27 型）: 完走は (着-1)/(n-1)、DNF/欠場は 1.0
        n_in_race = tr.groupby("race_key")["race_key"].transform("size").values.astype(float)
        fo_tr = pd.to_numeric(tr["finish_order"], errors="coerce").fillna(0).values
        reg_y = np.where(fo_tr >= 1, (fo_tr - 1) / np.maximum(n_in_race - 1, 1), 1.0)

        Xtr = tr[cols].values.astype(np.float64)
        Xte = te[cols].values.astype(np.float64)
        print(f"\n######## {wn} test={tf}〜{tt}  train {len(tr):,}行/{lay_tr['m']:,}R  "
              f"test {len(te):,}行/{lay_te['m']:,}R", flush=True)

        res: dict[str, list] = {}
        arms = ["baseline(binary top3)", "PL-K3", "PL-K6", "reg_rank"]
        for arm in arms:
            a3, a1, w, t3, pr, asm, afirm = [], [], [], [], [], [], []
            n = 0
            for seed in SEEDS:
                t0 = time.time()
                p = dict(PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
                if arm.startswith("baseline"):
                    ds = lgb.Dataset(Xtr, label=ytr3, free_raw_data=False)
                    bst = lgb.train(dict(p, objective="binary", metric="None"),
                                    ds, num_boost_round=N_ROUNDS)
                    p3 = bst.predict(Xte)
                    # 本番は 1着用に **別のモデル**(`lgbm_wt_win`)を持つ。同条件で比較する
                    dsw = lgb.Dataset(Xtr, label=tr["win_flag"].values, free_raw_data=False)
                    bstw = lgb.train(dict(p, objective="binary", metric="None"),
                                     dsw, num_boost_round=N_ROUNDS)
                    pw = bstw.predict(Xte)
                elif arm == "reg_rank":
                    ds = lgb.Dataset(Xtr, label=reg_y, free_raw_data=False)
                    bst = lgb.train(dict(p, objective="regression", metric="None"),
                                    ds, num_boost_round=N_ROUNDS)
                    p3 = -bst.predict(Xte)          # 小さいほど上位
                    pw = None
                else:
                    k = 3 if arm == "PL-K3" else 6
                    obj = PLObjective(lay_tr, k)
                    ds = lgb.Dataset(Xtr, label=np.zeros(len(Xtr)), free_raw_data=False)
                    bst = lgb.train(dict(p, objective=obj, metric="None",
                                         boost_from_average=False),
                                    ds, num_boost_round=N_ROUNDS)
                    sc = bst.predict(Xte)
                    pw, p3 = pl_probs(lay_te, sc)
                a3.append(roc_auc_score(yte3, p3))
                a1.append(roc_auc_score(yte1, pw if pw is not None else p3))
                x, y, z, n = race_metrics(te, p3, n7_te)
                w.append(x); t3.append(y); pr.append(z)
                if arm != "reg_rank":
                    mu, fr = axis_sum_stats(te, p3, n7_te)
                    asm.append(mu); afirm.append(fr)
                print(f"    [{arm} seed={seed}] {time.time()-t0:.0f}s", flush=True)
            res[arm] = (a3, a1, w, t3, pr)
            print(f"== {arm} ==", flush=True)
            print(f"   AUC(3着内)   {np.mean(a3):.5f} ± {np.std(a3):.5f}")
            print(f"   AUC(1着)     {np.mean(a1):.5f} ± {np.std(a1):.5f}")
            print(f"   1位勝率      {np.mean(w)*100:.2f}% ± {np.std(w)*100:.2f} (n={n:,})")
            print(f"   1位3着内     {np.mean(t3)*100:.2f}% ± {np.std(t3)*100:.2f}")
            print(f"   上位2車そろい {np.mean(pr)*100:.2f}% ± {np.std(pr)*100:.2f}", flush=True)
            if asm:
                print(f"   axis_sum 平均 {np.mean(asm):.4f} / >=1.44 の割合 "
                      f"{np.mean(afirm)*100:.2f}%", flush=True)
        b = res[arms[0]]
        for arm in arms[1:]:
            v = res[arm]
            print(f"== 差分（{arm} − baseline）==")
            print(f"   ΔAUC(3着内)  {np.mean(v[0])-np.mean(b[0]):+.5f}"
                  f"   (baseline seed SD {np.std(b[0]):.5f})")
            print(f"   ΔAUC(1着)    {np.mean(v[1])-np.mean(b[1]):+.5f}")
            print(f"   Δ1位勝率     {(np.mean(v[2])-np.mean(b[2]))*100:+.2f}pt")
            print(f"   Δ1位3着内    {(np.mean(v[3])-np.mean(b[3]))*100:+.2f}pt")
            print(f"   Δ上位2車そろい {(np.mean(v[4])-np.mean(b[4]))*100:+.2f}pt", flush=True)


if __name__ == "__main__":
    main()
