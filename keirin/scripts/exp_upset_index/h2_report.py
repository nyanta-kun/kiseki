#!/usr/bin/env python3
"""H2 の集計表を「探索窓・確認窓を横並び」にして合格判定する。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_upset_index/h2_report.py trio
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

D = Path(__file__).resolve().parents[2] / "data" / "exp_upset"
WALL = 0.7485


def main() -> None:
    """券種ごとに横並び表と合格セルを出す。"""
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 1000)
    for kind in sys.argv[1:]:
        df = pd.read_pickle(D / f"h2_{kind}.pkl")
        keys = ["cars", "metric", "q", "rule", "k", "staking"]
        vals = ["act100", "per_day", "hit", "roi", "roi_ex", "roi_ex_lo", "rand_roi", "diff_lo", "pay_med"]
        w = df.pivot_table(index=keys, columns="win", values=vals)
        w.columns = [f"{v}_{'E' if c.startswith('explore') else 'C'}" for v, c in w.columns]
        w = w.reset_index()
        w["pass"] = ((w.roi_ex_lo_E > WALL) & (w.roi_ex_lo_C > WALL)
                     & (w.diff_lo_E > 0) & (w.diff_lo_C > 0))
        print(f"\n######## {kind}: 合格 {int(w['pass'].sum())} / {len(w)} 構成")
        print(w[w["pass"]].round(3).to_string(index=False))
        cols = keys + ["act100_E", "act100_C", "per_day_E", "hit_E", "hit_C", "roi_E", "roi_C",
                       "roi_ex_lo_E", "roi_ex_lo_C", "rand_roi_E", "rand_roi_C", "diff_lo_E", "diff_lo_C"]
        for cars in (7, 9):
            sub = w[(w.cars == cars) & (w.staking == "dutch") & (w.rule != "top")]
            print(f"\n== {kind} {cars}車（ダッチ・100倍帯の買い目）両窓 ROI の小さい方で上位15 ==")
            sub = sub.assign(minroi=sub[["roi_E", "roi_C"]].min(axis=1)).sort_values("minroi", ascending=False)
            print(sub[cols].head(15).round(3).to_string(index=False))
            base = w[(w.cars == cars) & (w.metric == "none")]
            print(f"-- 選定なし（全レース）")
            print(base[keys + ["per_day_E", "hit_E", "hit_C", "roi_E", "roi_C"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
