"""三連単の予測オッズモデルは学習レース数で頭打ちか（2026-08-29・**頭打ち**）。

    KEIRIN_DB_URL=... PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/06_tf_learning_curve.py

本番の三連単モデルは `--max-races 12000` の間引きで **学習 7,145レース**しか使って
いない（三連複は 60,169レース）。「学習量が足りないから三連複より粗いのでは」を
潰すために、**同じ検証行（2026・honest）で学習レース数だけを変えて**比べる。

実測（2026-08-29）:
    2,000R  logMAE 0.1784 / ±2倍 82.64%
    4,000R         0.1764 / 83.09%
    7,145R         0.1747 / 83.46%   ← 現行本番と同じ量
   12,000R         0.1744 / 83.54%
   17,869R         0.1733 / 83.82%

2.5倍にして logMAE −0.0014（0.8%）。**学習量は律速ではない。**
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

import scripts.train_odds_prediction_tf as T  # noqa: E402
from src.odds_prediction_tf import FEATURE_NAMES  # noqa: E402

CACHE = REPO / "data" / "exp_cache" / "oddspred_tf_dataset_26k.pkl"
TRAIN_END = "2025-12-31"


def main() -> None:
    import lightgbm as lgb
    if CACHE.exists():
        df = pd.read_pickle(CACHE)
    else:
        t0 = time.time()
        df = T.build_dataset(26000)
        df["y"] = np.log10(df.odds)
        for c in FEATURE_NAMES:
            df[c] = df[c].astype("float32")
        df.to_pickle(CACHE)
        print(f"dataset {df.shape} races={df.rk.nunique()} {time.time()-t0:.0f}s", flush=True)
    tr_all, te = df[df.date <= TRAIN_END], df[df.date > TRAIN_END]
    keys = np.array(sorted(tr_all.rk.unique()))
    print(f"学習に使えるレース {len(keys)} / 検証 {te.rk.nunique()}R {len(te)}行", flush=True)
    params = dict(objective="regression", metric="l1", learning_rate=0.05, num_leaves=63,
                  min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
                  bagging_freq=1, verbose=-1, num_threads=8)
    X, y = te[list(FEATURE_NAMES)], te.odds.to_numpy()
    for n in (2000, 4000, 7145, 12000, len(keys)):
        if n > len(keys):
            continue
        # 期間を偏らせないよう等間隔で間引く（本番の build_dataset と同じ作法）
        sel = set(keys[sorted(set(np.linspace(0, len(keys) - 1, n).astype(int)))])
        tr = tr_all[tr_all.rk.isin(sel)]
        b = lgb.train(params, lgb.Dataset(tr[list(FEATURE_NAMES)], tr.y), num_boost_round=600)
        tgt = float(tr.groupby("rk").odds.apply(lambda s: (1 / s).sum()).mean())
        raw = np.clip(10 ** b.predict(X), 1.0, None)
        pred = raw * (pd.Series(1 / raw).groupby(te.rk.to_numpy()).transform("sum").to_numpy() / tgt)
        e = np.abs(np.log10(y / pred))
        print(f"学習{tr.rk.nunique():>6}R({len(tr):>8}行): logMAE {e.mean():.4f} "
              f"±2倍 {100*(e<np.log10(2)).mean():.2f}%  中央比 {np.median(y/pred):.3f}", flush=True)


if __name__ == "__main__":
    if not os.environ.get("KEIRIN_DB_URL"):
        raise SystemExit("KEIRIN_DB_URL が未設定です")
    main()
