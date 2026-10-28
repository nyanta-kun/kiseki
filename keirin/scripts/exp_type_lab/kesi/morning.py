#!/usr/bin/env python3
"""探索（事前登録の判定外）: 朝 6 時台のオッズを「朝の市場」として出発点にしたとき、
消しモデルのずれが最終市場に対しても残るか。窓 2026-06-08〜10-27（10/28 の1変数集計で一度見た期間）。

    .venv/bin/python scripts/exp_type_lab/kesi/morning.py /tmp/claude-501/kesi
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as R  # noqa: E402


def main():
    d = Path(sys.argv[1])
    df = pd.read_pickle(d / "kesi_feats.pkl")
    df = df[df.race_date >= "2024-07-01"].reset_index(drop=True)
    am = pd.read_csv(d / "morning.csv", dtype={"race_key": str})
    m, _ = R.fit(df, R.BASE_FEATS, "2024-07-01", "2025-12-31", "2025-10-01")
    t = df[df.race_date >= "2026-06-01"].merge(am, on=["race_key", "frame_no"], how="inner")
    print("朝のオッズあり", t.race_key.nunique(), "R", flush=True)
    print("logit 相関 朝↔最終:", round(np.corrcoef(R.logit(t.m3_am), R.logit(t.m3))[0, 1], 4),
          " 平均絶対差(確率):", round((t.m3_am - t.m3).abs().mean(), 4))
    # 朝の市場そのものの較正（最終に対して）
    t["dam"] = pd.qcut(t.m3_am.rank(method="first"), 10, labels=False)
    print("朝の見込み十分位: 実際/Σ朝m3 と 実際/Σ最終m3")
    print(t.groupby("dam").apply(lambda g: pd.Series(dict(m3_am=g.m3_am.mean(), act=g.top3.mean(),
                                                           vs_am=g.top3.sum() / g.m3_am.sum(),
                                                           vs_fin=g.top3.sum() / g.m3.sum()))).round(3).to_string())
    # モデル: 出発点を朝の m3 に替える（特徴の logit_m3・m3_rank も朝の値へ）
    u = t.copy()
    u["m3"] = u["m3_am"]
    u["logit_m3"] = R.logit(u.m3)
    u["m3_rank"] = u.groupby("race_key")["m3"].rank(ascending=False, method="min")
    p = R.predict(m, u, R.BASE_FEATS)
    t["r_am"] = p / t.m3_am.clip(0.005, 0.995)
    s = t[t.m3_am >= 0.15].copy()
    s["dec"] = pd.qcut(s.r_am.rank(method="first"), 10, labels=False)
    print("m3_am≥0.15 の r̂(朝) 十分位: 実際/Σ朝m3 と 実際/Σ最終m3（払戻は最終で決まる）")
    print(s.groupby("dec").apply(lambda g: pd.Series(dict(n=len(g), r=g.r_am.mean(), m3_am=g.m3_am.mean(),
                                                           m3_fin=g.m3.mean(), act=g.top3.mean(),
                                                           vs_am=g.top3.sum() / g.m3_am.sum(),
                                                           vs_fin=g.top3.sum() / g.m3.sum()))).round(3).to_string())


if __name__ == "__main__":
    main()
