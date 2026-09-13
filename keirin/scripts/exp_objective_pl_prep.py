#!/usr/bin/env python3
"""目的関数 A/B 用の台を作る（2026-09-14）。

`data/feature_cache/wtfeat_20221201_*_f70_*.pkl`（`load_features_wt` が作る既存の
特徴量キャッシュ）から、必要列だけを残して日付で切った小さい pickle を2本作る。

  /tmp/plab/feat.pkl       2024-04-01〜2026-07-15（モデル層 A/B 用）
  /tmp/plab/feat_full.pkl  2024-04-01〜2026-08-31（商品KPI 用）

⚠️ **期間キャッシュの切り出しは厳密には安全でない**（`feature_wt.py` の
   「なぜ期間ごとに持つのか」参照: `race_point` の欠損補完が読み込み範囲全体の
   中央値を使うため、対象 0.34% の行で値が変わる）。ここでは **全腕に同じ台を
   使う A/B** なので影響しない。実測でも baseline の AUC は直接構築版
   （`model_headroom_2026_09_14.md` の 0.77867 / 0.78088）と
   0.77888 / 0.78149 で一致している。

    PYTHONPATH=. .venv/bin/python scripts/exp_objective_pl_prep.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_CACHE_DIR, FEATURE_COLS_WT, TARGET_COL_WT)

OUT = Path("/tmp/plab")
KEEP_EXTRA = [TARGET_COL_WT, "race_key", "race_date", "frame_no", "finish_order"]


def main() -> None:
    cands = sorted(FEATURE_CACHE_DIR.glob(f"wtfeat_20221201_*_f{len(FEATURE_COLS_WT)}_*.pkl"))
    if not cands:
        raise SystemExit(f"特徴量キャッシュが無い: {FEATURE_CACHE_DIR}\n"
                         f"KEIRIN_FEATURE_CACHE=1 で一度 build すること")
    src = cands[-1]
    print(f"元: {src.name}", flush=True)
    df = pd.read_pickle(src)
    keep = list(dict.fromkeys(list(FEATURE_COLS_WT) + KEEP_EXTRA))
    miss = [c for c in keep if c not in df.columns]
    if miss:
        raise SystemExit(f"列が足りない: {miss}")
    df = df[keep]
    OUT.mkdir(parents=True, exist_ok=True)
    for name, hi in (("feat.pkl", "2026-07-15"), ("feat_full.pkl", "2026-08-31")):
        d = df[(df["race_date"] >= "2024-04-01") & (df["race_date"] <= hi)]
        d.to_pickle(OUT / name)
        print(f"  {name}: {len(d):,}行 / {d['race_key'].nunique():,}レース", flush=True)


if __name__ == "__main__":
    main()
