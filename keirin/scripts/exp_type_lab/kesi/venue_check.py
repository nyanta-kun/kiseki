#!/usr/bin/env python3
"""事後の確認（判定外）: 寄与 1 位の「場」は人工物か。

1. 主モデルの対数損失の改善を場ごとに分解（確認(主)）
2. venue_id を外して学習し直し、r̂ 十分位の表が保たれるか（主・副）

    .venv/bin/python scripts/exp_type_lab/kesi/venue_check.py /tmp/claude-501/kesi
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as R  # noqa: E402


def deciles(d, p, label):
    d = d.assign(r=p / d.m3.clip(0.005, 0.995))
    s = d[d.m3 >= 0.15].copy()
    s["dec"] = pd.qcut(s.r.rank(method="first"), 10, labels=False)
    t = s.groupby("dec").agg(r=("r", "mean"), act=("top3", "sum"), m3=("m3", "sum"))
    t["ratio"] = t.act / t.m3
    ll_m = R.ll(d.top3.values, d.m3.clip(0.005, 0.995).values).sum() / d.race_key.nunique()
    ll_p = R.ll(d.top3.values, p).sum() / d.race_key.nunique()
    print(f"[{label}] Δlogloss/R {ll_p - ll_m:+.4f}  十分位 r̂→実際/見込み: "
          + " ".join(f"{a:.2f}→{b:.2f}" for a, b in zip(t.r, t.ratio)), flush=True)


def main():
    d0 = Path(sys.argv[1])
    df = pd.read_pickle(d0 / "kesi_feats.pkl")
    df = df[df.race_date >= "2024-07-01"].reset_index(drop=True)
    win = {"主": ("2026-01-01", "2026-05-31"), "副": ("2026-06-01", "2026-10-27")}
    m, _ = R.fit(df, R.BASE_FEATS, "2024-07-01", "2025-12-31", "2025-10-01")
    a, b = win["主"]
    d = df[(df.race_date >= a) & (df.race_date <= b)].copy()
    d["dl"] = R.ll(d.top3.values, R.predict(m, d, R.BASE_FEATS)) - R.ll(d.top3.values, d.m3.clip(0.005, 0.995).values)
    v = d.groupby("venue_id", observed=True).agg(R_=("race_key", "nunique"), dl=("dl", "sum"))
    v["dl_per_R"] = v.dl / v.R_
    v["share"] = v.dl / v.dl.sum()
    print("場ごとの改善（確認(主)・負が改善）: 改善の大きい順 上位8 / 全", len(v), "場")
    print(v.sort_values("dl").head(8).round(4).to_string())
    print("改善の上位4場の占める割合:", round(float(v.sort_values("dl").head(4).dl.sum() / v.dl.sum()), 3))
    feats = [f for f in R.BASE_FEATS if f != "venue_id"]
    m2, _ = R.fit(df, feats, "2024-07-01", "2025-12-31", "2025-10-01")
    for k, (a, b) in win.items():
        d = df[(df.race_date >= a) & (df.race_date <= b)]
        deciles(d, R.predict(m, d, R.BASE_FEATS), f"{k} 場あり")
        deciles(d, R.predict(m2, d, feats), f"{k} 場なし")
    imp = pd.Series(m2.feature_importance("gain"), index=feats).sort_values(ascending=False)
    print("場なしの寄与上位:", ", ".join(f"{k} {x / imp.sum() * 100:.1f}%" for k, x in imp.head(10).items()))


if __name__ == "__main__":
    main()
