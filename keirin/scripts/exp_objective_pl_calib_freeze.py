#!/usr/bin/env python3
"""PL-K3 を TEST へ載せる前に、較正の絶対閾値を**探索側で凍結**する（2026-09-14）。

`docs/PREREG_PL_OBJECTIVE_2026_09_14.md` の空欄を埋めるための計算。

## なぜ要るか

PL は確率が上に寄る（十分位の実測/予測 q9 0.942 ↔ q0 1.146）。`axis_sum`（p3 上位2車の和）
の分布が動くので、**絶対閾値の `AXIS_SUM_FIRM`(=1.44) と `AXIS_GATE_MIN`(4プランの p20) を
そのままにしてモデルだけ差し替えると、別の商品を売ることになる。**

## 🔴 どう凍結するか

**「同じ分位」で移す。** 現行 baseline の下で 1.44 が探索側の何分位かを求め、
PL-K3 の下で**その同じ分位**にあたる値を新しい閾値にする。
＝ **型の割合（堅い/混戦の比）を保つ**。値そのものを引き継ぐのではない。

🔴 **TEST 期間（2026-10-01〜）のデータは1行も使わない。** 使うと一度きりでなくなる。
🔴 **予測は walk-forward の out-of-sample だけ**を使う（四半期ごとに
   `TRAIN_FROM`〜その四半期の直前で学習 → その四半期を予測）。

    PYTHONPATH=. .venv/bin/python scripts/exp_objective_pl_calib_freeze.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import exp_objective_pl_ab as M  # noqa: E402

#: 🔴 TEST は 2026-10-01 から。**それ以降を1行も含めない。**
OOS_QUARTERS = [
    ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
    ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
    ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30"),
    ("2026-07-01", "2026-08-31"),   # 台の終端（feat_full.pkl）
]
SEED = 42
FEAT = Path("/tmp/plab/feat_full.pkl")


def axis_sum_of(df: pd.DataFrame, p3: np.ndarray) -> pd.DataFrame:
    """レースごとに p3 上位2車の和（＝`race_shape` の `axis_sum`）を出す。7車のみ。"""
    t = df[["race_key"]].copy()
    t["p3"] = p3
    n7 = t.groupby("race_key")["race_key"].transform("size") == 7
    t = t[n7]
    g = t.groupby("race_key")["p3"]
    return g.apply(lambda s: float(np.sort(s.values)[-2:].sum())).rename("axis_sum").reset_index()


def main() -> None:
    df = pd.read_pickle(FEAT)
    df = df[df["finish_order"].notna()].copy()
    cols = list(M.FEATURE_COLS_WT)
    P = dict(M.PARAMS, seed=SEED, bagging_seed=SEED, feature_fraction_seed=SEED)

    rows_b, rows_p = [], []
    for tf, tt in OOS_QUARTERS:
        tr = df[(df["race_date"] >= M.TRAIN_FROM) & (df["race_date"] < tf)]
        te = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)]
        if len(tr) < 50_000 or te.empty:
            print(f"  skip {tf}〜{tt}  train={len(tr):,} test={len(te):,}", flush=True)
            continue
        tr = tr.reset_index(drop=True)
        te = te.reset_index(drop=True)
        print(f"  {tf}〜{tt}  train {len(tr):,} / test {len(te):,}", flush=True)
        Xtr = tr[cols].values.astype(np.float64)
        Xte = te[cols].values.astype(np.float64)

        ds = lgb.Dataset(Xtr, label=tr[M.TARGET_COL_WT].values, free_raw_data=False)
        b0 = lgb.train(dict(P, objective="binary", metric="None"), ds, num_boost_round=500)
        rows_b.append(axis_sum_of(te, b0.predict(Xte)))

        lay_tr, lay_te = M.race_layout(tr), M.race_layout(te)
        obj = M.PLObjective(lay_tr, 3)
        ds2 = lgb.Dataset(Xtr, label=np.zeros(len(Xtr)), free_raw_data=False)
        b1 = lgb.train(dict(P, objective=obj, metric="None", boost_from_average=False),
                       ds2, num_boost_round=500)
        _, p3p = M.pl_probs(lay_te, b1.predict(Xte))
        rows_p.append(axis_sum_of(te, p3p))

    B = pd.concat(rows_b).set_index("race_key")["axis_sum"]
    Pl = pd.concat(rows_p).set_index("race_key")["axis_sum"]
    common = B.index.intersection(Pl.index)
    B, Pl = B.loc[common], Pl.loc[common]
    print(f"\nOOS レース {len(B):,}（2025-01-01〜2026-08-31・7車・walk-forward）")

    cur = 1.44
    q = float((B < cur).mean())
    new = float(np.quantile(Pl.values, q))
    print(f"\n== AXIS_SUM_FIRM ==")
    print(f"  現行 baseline  1.44  → 探索側の分位 {q*100:.2f}%"
          f"（堅い側 {100-q*100:.2f}%）")
    print(f"  baseline 平均 {B.mean():.4f} / PL-K3 平均 {Pl.mean():.4f}")
    print(f"  🔴 凍結値  AXIS_SUM_FIRM = {new:.4f}"
          f"   （PL-K3 の同分位・堅い側は {100*(Pl >= new).mean():.2f}%）")
    print(f"  参考: 閾値を 1.44 のまま据え置くと堅い側が"
          f" {100*(Pl >= cur).mean():.2f}% へ膨らむ")

    print(f"\n== 分布の比較（分位点）==")
    print(f"{'分位':>6}{'baseline':>11}{'PL-K3':>10}")
    for p in (5, 10, 25, 50, 75, 90, 95):
        print(f"{p:>5}%{np.quantile(B.values, p/100):11.4f}{np.quantile(Pl.values, p/100):10.4f}")


if __name__ == "__main__":
    main()
