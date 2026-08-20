#!/usr/bin/env python3
"""【N-7 前処理】現行の買い方に揃えた 7C / 7S の推奨を全期間で作り直す（読み取り専用）。

## なぜ要るか

`picks_history` は**商品世代が混ざっている**。当月しか毎朝再構築されないため、
仕様変更より前の月は古い買い方のまま残る。

- **7C**: `strategy_wt.py:448`「2026-08-17 三連単への切替を停止（ユーザー判断）」
  → 01〜07 は三連単 22〜34% / 08 は 0%。三連単を除外して集計すると、
    現行なら三連複で出るはずの**高 pw レースが丸ごと欠ける**
- **7S**: 2026-08-14 に 旧7S + 旧7A + 旧7SS を統合

足切り閾値の掃引は「堅い側を落とす」操作なので、母集団の堅い側が欠けたままだと
件数減を過小評価する。**現行コード + 月次 vintage で全期間を作り直してから測る。**

🔴 DB へは書き込まない。`build_rows` は SELECT のみで、結果は pickle に落とす。
   （`rebuild_*_walkforward_pg.py` は DELETE→INSERT するので呼ばない）

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_regen_picks_current_plan.py --ranks 7c,7s
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.wt_rebuild_common import split_by_model_availability  # noqa: E402
from src.wt_vintage_config import bad_model_name, monthly_windows  # noqa: E402

OUT = REPO / "data" / "exp_cache"


def regen_7c(windows) -> list[dict]:
    from scripts.backfill_7c_rank_wt import build_rows
    rows = []
    for d1, d2, ev, wi in windows:
        r = build_rows(ev, d1, d2, wi)
        tf = sum(1 for x in r if str(x.get("pred_combo", "")).startswith("三単"))
        print(f"  7C {d1[:7]}  {len(r):>4}件（三連単 {tf}）", flush=True)
        rows += r
    return rows


def regen_7s(windows) -> list[dict]:
    # 🔴 RANK_7S は 旧7S+旧7A+旧7SS の**統合ランク**。正本の rebuild が使うのは
    #    merged 版で、統合前の backfill_7s_rank_wt だと件数が 1/5 になる。
    from scripts.backfill_7s_merged_rank_wt import build_rows
    rows = []
    for d1, d2, ev, wi in windows:
        r = build_rows(ev, d1, d2, win_model_name=wi, bad_model_name=bad_model_name(ev))
        print(f"  7S {d1[:7]}  {len(r):>4}件", flush=True)
        rows += r
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ranks", default="7c,7s")
    ap.add_argument("--from-month", default="2026-01")
    a = ap.parse_args()

    windows = [w for w in monthly_windows() if w[0][:7] >= a.from_month]
    avail, missing = split_by_model_availability(windows, require_bad=True)
    if missing:
        print(f"⚠️ vintage 不足で除外: {[(w[0][:7], n) for w, n in missing]}")
    print(f"[regen] 窓 {len(avail)}本  {avail[0][0]}〜{avail[-1][1]}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    for rank in a.ranks.split(","):
        fn = {"7c": regen_7c, "7s": regen_7s}[rank]
        print(f"[regen] RANK_{rank.upper()} ...", flush=True)
        rows = fn(avail)
        p = OUT / f"regen_{rank}_current_plan.pkl"
        p.write_bytes(pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL))
        print(f"[regen] RANK_{rank.upper()} 計 {len(rows):,}件 → {p}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
