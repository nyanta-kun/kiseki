#!/usr/bin/env python3
"""三連複の相手順位の検証を **2024-01〜** へ広げるための軽量キャッシュ。

## なぜ別に作るのか

`exp_tf_shape_cache.py` は三連単の予測オッズ板（`odds_tf_n7.txt`・学習終端
2025-12-31・**vintage なし**）を作るため **2026-01 以降しか honest に作れない**。

だが **順位5の規則は `p3`（3着内率）の順位しか使わない**。三連単オッズモデルも
`pw` も要らないので、その制約に縛られる必要がない。
→ **2024-01〜2025-12 を、今回まったく触っていない独立窓として使える**
（現行窓 14,941R の約3倍）。

出すのは1レース1行の最小限:  race_key / race_date / p3 降順の車番列。
板と結果は解析時に DB から引く。

🔴 必ず月次凍結 vintage で回す（本番モデルを過去へ当てると in-sample）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.build_7t1_candidates as b7t1  # noqa: E402
from src.strategy_wt import RANK_7T1_NE  # noqa: E402
from src.wt_vintage_config import monthly_windows  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--from-month", default="2024-01")
    ap.add_argument("--to-month", default="2025-12")
    args = ap.parse_args()

    windows = [w for w in monthly_windows()
               if args.from_month <= w[0][:7] <= args.to_month]
    if not windows:
        print("対象窓なし")
        return 1
    print(f"窓 {len(windows)}本: {windows[0][0]}〜{windows[-1][1]}")

    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w") as f:
        for df, dt, em, wm in windows:
            by_race = b7t1._load_range(df, dt)
            if not by_race:
                print(f"  {df}〜{dt}: 0R")
                continue
            p3_all, _ = b7t1._predict(df, dt, em, wm)
            c = 0
            for rk, ents in by_race.items():
                p3 = p3_all.get(rk)
                if not p3 or len(p3) != RANK_7T1_NE:
                    continue
                order = [k for k, _ in sorted(p3.items(), key=lambda kv: (-kv[1], kv[0]))]
                f.write(json.dumps({
                    "race_key": rk, "race_date": ents[0]["race_date"],
                    "order": order,
                    "p3": {str(k): round(v, 6) for k, v in p3.items()},
                }, ensure_ascii=False) + "\n")
                c += 1
            n += c
            print(f"  {df}〜{dt} [{em}]: {c}R", flush=True)
    print(f"→ {out} に {n}R")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
