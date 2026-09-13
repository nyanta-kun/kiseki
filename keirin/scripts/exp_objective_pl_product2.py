#!/usr/bin/env python3
"""PL-K3 が商品で負ける原因の切り分け（2026-09-14）。

`exp_objective_pl_product.py` で **PL-K3 はモデル指標では勝つのに商品では負ける**
（表示的中 −1.10pt）ことが出た。原因候補を2つに割る:

  ① **PL の pw（1着強度）が弱い**    → `rank_7t3_blend_probs` の1着側が劣化する
     → 腕「PL の p3 × 現行 binary の pw」
  ② **PL の p3 が上に寄りすぎている**（較正）→ 温度で寝かせる
     → 腕「PL-K3 T=1.4」（raw score を T で割るだけ。**レース内の順位は不変**）

比較は全部 **型境界（`AXIS_SUM_FIRM`）と軸信頼ゲートを腕ごとに引き直した**公平版。

    PYTHONPATH=. .venv/bin/python scripts/exp_objective_pl_product2.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import exp_objective_pl_ab as M  # noqa: E402
import exp_objective_pl_product as P  # noqa: E402
import scripts.exp_type_lab.common as C  # noqa: E402

SEEDS = [42, 101]
TEMPS = (1.4,)


def fit_all(seed: int):
    df = pd.read_pickle(P.FEAT)
    df = df[df["finish_order"].notna()].copy()
    tr = df[(df["race_date"] >= P.TRAIN_FROM) & (df["race_date"] <= P.TRAIN_TO)].reset_index(drop=True)
    te = df[(df["race_date"] > P.TRAIN_TO)].reset_index(drop=True)
    cols = list(M.FEATURE_COLS_WT)
    Xtr, Xte = tr[cols].values.astype(np.float64), te[cols].values.astype(np.float64)
    p = dict(M.PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
    y3 = tr[M.TARGET_COL_WT].values
    y1 = (pd.to_numeric(tr["finish_order"], errors="coerce") == 1).astype(int).values
    b3 = lgb.train(dict(p, objective="binary", metric="None"),
                   lgb.Dataset(Xtr, label=y3, free_raw_data=False), num_boost_round=500)
    b1 = lgb.train(dict(p, objective="binary", metric="None"),
                   lgb.Dataset(Xtr, label=y1, free_raw_data=False), num_boost_round=500)
    p3b, pwb = b3.predict(Xte), b1.predict(Xte)
    lay_tr, lay_te = M.race_layout(tr), M.race_layout(te)
    pl = lgb.train(dict(p, objective=M.PLObjective(lay_tr, 3), metric="None",
                        boost_from_average=False),
                   lgb.Dataset(Xtr, label=np.zeros(len(Xtr)), free_raw_data=False),
                   num_boost_round=500)
    sc = pl.predict(Xte)
    pwp, p3p = M.pl_probs(lay_te, sc)
    out = {"binary(現行)": (p3b, pwb), "PL-K3": (p3p, pwp),
           "PL の p3 × binary の pw": (p3p, pwb)}
    for t in TEMPS:
        pw_t, p3_t = M.pl_probs(lay_te, sc / t)
        out[f"PL-K3 T={t}"] = (p3_t, pw_t)
    return te[["race_key", "frame_no"]], out


def main() -> None:
    z = {k: v for k, v in np.load("/tmp/race_type_board.npz", allow_pickle=True).items()}
    idx = C.select(None, "confirm")
    idx = np.array([i for i in idx if z["TYPE"][i] in "ABCDEF"])
    nd = len(set(z["DATE"][idx]))
    target_firm = float(np.mean([str(z["TYPE"][i]) in "ABC" for i in idx]))
    print(f"確認窓 {len(idx):,}R / {nd}日 / 堅い側 {target_firm*100:.2f}%", flush=True)
    agg: dict[str, list] = defaultdict(list)
    for seed in SEEDS:
        print(f"\n#### seed={seed}", flush=True)
        meta, preds = fit_all(seed)
        for arm, (p3, pw) in preds.items():
            vecs = P.as_board_vecs(meta, p3, pw)
            axs = []
            for i in idx:
                v = vecs.get(str(z["KEY"][i]))
                if v is not None:
                    a = np.sort(v[0])[::-1]
                    axs.append(a[0] + a[1])
            ft = float(np.percentile(axs, 100 * (1 - target_firm)))
            recs = P.run(arm, idx, vecs, z, None, ft)
            byp: dict[str, list] = defaultdict(list)
            for r in recs:
                byp[r["plan"]].append(r["axis"])
            thr = {k: float(np.percentile(v, 20)) for k, v in byp.items() if len(v) >= 50}
            recs = [dict(r, passed=r["axis"] >= thr.get(r["plan"], 0.0)) for r in recs]
            print(f"  [{arm}] axis_sum 平均 {np.mean(axs):.4f} / 型境界 {ft:.4f}", flush=True)
            agg[arm].append(P.show(arm, recs, nd))
    print("\n==== seed 平均（型境界・軸ゲートとも腕ごとに引き直し）====")
    for k, ts in agg.items():
        f = lambda n: np.mean([t.get(n, 0) for t in ts])  # noqa: E731
        print(f"  {k:26s} 件/日 {f('perday'):5.2f} 表示的中 {f('shown'):6.2f}% "
              f"ROI {f('roi'):6.1f}% 払戻中央 {f('med_pay'):8.0f} "
              f"10万+/日 {f('big_per_day'):.3f}")


if __name__ == "__main__":
    main()
