#!/usr/bin/env python3
"""6車の walk-forward 予測を作る（キャッシュが無いため・2026-08-27）。

🔴 **本番モデルを過去へ当てない。** その月に使ってよい月次 vintage
   （`lgbm_wt_eval_mYYMM` / `lgbm_wt_win_mYYMM`）で月ごとに予測する。
   `build_type_lab_picks.run_paper_vintage` と同じ考え方。

出力: `data/exp_cache/wf_preds6_{from}_{to}.pkl`（race_key, frame_no, pp3, ppw）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

import os
N_CAR = int(os.environ.get("EXP_N_CAR", "6"))


def months(d1: str, d2: str):
    y, m = int(d1[:4]), int(d1[5:7])
    while True:
        first = date(y, m, 1)
        nxt = date(y + (m == 12), (m % 12) + 1, 1)
        last = date.fromordinal(nxt.toordinal() - 1)
        if first.isoformat() > d2:
            return
        yield (max(first.isoformat(), d1), min(last.isoformat(), d2),
               f"{y % 100:02d}{m:02d}")
        y, m = nxt.year, nxt.month


def keys_of(d1: str, d2: str) -> set[str]:
    with get_connection() as c:
        return {str(r[0]) for r in c.execute(
            "SELECT race_key FROM wt_races WHERE race_date BETWEEN ? AND ? "
            "AND n_entries = ?", (d1, d2, N_CAR)).fetchall()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2024-07-01")
    ap.add_argument("--to", dest="d2", default="2026-08-04")
    a = ap.parse_args()

    from src.models.trainer import load_model
    from src.preprocessing.feature_wt import (
        build_features_wt, load_raw_data_wt, prepare_X,
    )

    out = []
    for m1, m2, tag in months(a.d1, a.d2):
        keys = keys_of(m1, m2)
        if not keys:
            print(f"  {m1}〜{m2}: 6車なし")
            continue
        try:
            ev = load_model(f"lgbm_wt_eval_m{tag}")
            wn = load_model(f"lgbm_wt_win_m{tag}")
        except Exception as e:  # noqa: BLE001
            print(f"  🔴 {tag}: vintage モデルが無い（{e!r}）→ この月は捨てる")
            continue
        feats = build_features_wt(load_raw_data_wt(min_date=m1, max_date=m2))
        if feats is None or not len(feats):
            print(f"  {m1}〜{m2}: 特徴量なし")
            continue
        feats = feats[feats["race_key"].astype(str).isin(keys)]
        if not len(feats):
            print(f"  {m1}〜{m2}: 6車の行なし")
            continue
        X = prepare_X(feats)
        out.append(pd.DataFrame({
            "race_key": feats["race_key"].astype(str).values,
            "frame_no": feats["frame_no"].astype(int).values,
            "pp3": ev.predict_proba(X)[:, 1],
            "ppw": wn.predict_proba(X)[:, 1],
        }))
        print(f"  {m1}〜{m2}: {len(feats)}行 / {feats['race_key'].nunique()}R  (m{tag})")

    if not out:
        print("🔴 1行も作れなかった")
        return
    df = pd.concat(out, ignore_index=True)
    dst = REPO / "data" / "exp_cache" / f"wf_preds{N_CAR}_{a.d1}_{a.d2}.pkl"
    df.to_pickle(dst)
    print(f"保存 {len(df)}行 / {df['race_key'].nunique()}R → {dst}")


if __name__ == "__main__":
    main()
