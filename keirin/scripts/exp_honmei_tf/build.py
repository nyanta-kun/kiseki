#!/usr/bin/env python3
"""本命絡み三連単の検証台を作る（2026-08-26）。

`/tmp/tf20_board.npz`（vintage walk-forward の 210点板）に
各レースの p3 / pw（車番1..7）を足して `/tmp/honmei_tf.npz` を書く。

⚠️ 予測オッズモデル `odds_tf_n7` の train_end は 2025-12-31。
   2024-25 窓は in-sample。確認は 2026 窓で行うこと。
"""
from __future__ import annotations
import glob, os, pickle, sys, time
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); os.chdir(REPO)

t0 = time.time()
z = np.load("/tmp/tf20_board.npz", allow_pickle=True)
KEY = z["KEY"]
idx = {k: i for i, k in enumerate(KEY)}
P3 = np.full((len(KEY), 7), np.nan, np.float32)
PW = np.full((len(KEY), 7), np.nan, np.float32)

for f in sorted(glob.glob("data/exp_cache/wf_preds_2*.pkl")):
    df = pickle.load(open(f, "rb"))
    for rk, fn, p3, pw in zip(df.race_key, df.frame_no, df.pp3, df.ppw):
        i = idx.get(rk)
        if i is None:
            continue
        c = int(fn)
        if 1 <= c <= 7:
            P3[i, c - 1] = p3
            PW[i, c - 1] = pw
    print(f"  {os.path.basename(f)}  {time.time()-t0:.0f}s", flush=True)

ok = np.isfinite(P3).all(1) & np.isfinite(PW).all(1)
print(f"p3/pw そろい {ok.sum():,}/{len(KEY):,}")
out = {k: z[k] for k in z.files}
out["P3"] = P3
out["PW"] = PW
out["OKPRED"] = ok
np.savez_compressed("/tmp/honmei_tf.npz", **out)
print(f"保存 /tmp/honmei_tf.npz  {time.time()-t0:.0f}s")
