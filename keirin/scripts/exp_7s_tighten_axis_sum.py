#!/usr/bin/env python3
"""7S の選別を厳しくする掃引（`RANK_7S_AXIS_SUM_MAX` を下げる）・2026-08-19。

## なぜ picks_history から測るのか

7S の軸選定は3ヘッド（`pred_bad` が必要）で `wt_entries` からは再現できない。
一方 `pred_combo` に **`(axis_sum=1.4)` が記録されている**ので、7S が実際に
選んだレースの axis_sum はそこから読める。

🔴 **厳しくする方向（部分集合を取る）ならこれで足りる**。緩める方向は
   「落としたレース」が picks_history に無いので測れない。

🔴 **いま `wt_entries` の指数はバックフィル中で新旧が混在している**（2026-08-19）。
   そこから axis_sum を計算し直すと母集団が壊れる。picks_history の記録値は
   当時の指数で確定しているので影響を受けない。

⚠️ ただし記録値は**60特徴時代の指数**。66特徴へ移行後は axis_sum の分布が動くので、
   **ここで選んだ値は段5で新しい分布の上に再確認すること**。ここで分かるのは
   「厳しくするとどうトレードオフするか」の形であって、最終的な数値ではない。

⚠️ `rule_version` の世代混在に注意（7S は 2026-08-14 に 7A/7SS を統合）。
   統合後の行だけに絞るオプションを持たせる。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7s_tighten_axis_sum.py \
        --from 2025-01-01 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.strategy_wt import RANK_7S_AXIS_SUM_MAX  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    ap.add_argument("--since-merge", action="store_true",
                    help="7A/7SS 統合後（2026-08-14〜）だけに絞る")
    a = ap.parse_args()
    d1 = "2026-08-14" if a.since_merge else a.d1

    with get_connection() as conn:
        cur = conn.execute(
            "SELECT race_date, pred_combo, bet_amount, payout, hit "
            "FROM picks_history WHERE rank = 'RANK_7S' AND bet_amount > 0 "
            "  AND race_date BETWEEN ? AND ?", (d1, a.d2))
        rows = []
        for d, combo, bet, pay, hit in cur.fetchall():
            m = re.search(r"axis_sum=([0-9.]+)", combo or "")
            if not m:
                continue
            rows.append(dict(date=d, s=float(m.group(1)), bet=int(bet or 0),
                             pay=int(pay or 0), hit=int(hit or 0)))
    if not rows:
        print("対象0件"); return 0
    days = len({r["date"] for r in rows})
    print(f"\n7S {len(rows)}R / {days}日 [{d1}〜{a.d2}]"
          f"{'  ※7A/7SS統合後のみ' if a.since_merge else ''}")
    print(f"  現行 RANK_7S_AXIS_SUM_MAX = {RANK_7S_AXIS_SUM_MAX}")
    ss = sorted(r["s"] for r in rows)
    print(f"  記録された axis_sum: 中央 {statistics.median(ss):.3f} / "
          f"25%点 {ss[len(ss)//4]:.3f} / 75%点 {ss[3*len(ss)//4]:.3f} / 最大 {max(ss):.3f}")

    print(f"\n  {'閾値':>8}{'R':>7}{'件/日':>8}{'残存%':>8}{'的中%':>8}"
          f"{'表示的中%':>11}{'ROI%':>8}{'倍率中央':>9}")
    base_n = len(rows)
    for th in (1.40, 1.35, 1.30, 1.25, 1.20, 1.15, 1.10, 1.05, 1.00):
        sub = [r for r in rows if r["s"] <= th + 1e-9]
        if len(sub) < 30:
            continue
        bet = sum(r["bet"] for r in sub); pay = sum(r["pay"] for r in sub)
        hit = sum(1 for r in sub if r["pay"] > 0)
        net = sum(1 for r in sub if r["pay"] >= r["bet"] and r["pay"] > 0)
        rat = [r["pay"] / r["bet"] for r in sub if r["pay"] > 0]
        mark = " ←現行" if abs(th - RANK_7S_AXIS_SUM_MAX) < 1e-9 else ""
        print(f"  {th:>8.2f}{len(sub):>7}{len(sub)/days:>8.2f}"
              f"{100*len(sub)/base_n:>8.1f}{100*hit/len(sub):>8.1f}"
              f"{100*net/len(sub):>11.1f}{100*pay/bet:>8.1f}"
              f"{(statistics.median(rat) if rat else 0):>9.2f}{mark}")

    print("\n  === 年別（主要な閾値のみ・符号の一貫性を見る）===")
    for y in ("2025", "2026"):
        sub_y = [r for r in rows if r["date"].startswith(y)]
        if len(sub_y) < 100:
            continue
        dy = len({r["date"] for r in sub_y})
        print(f"\n  [{y}] {len(sub_y)}R / {dy}日")
        print(f"  {'閾値':>8}{'R':>7}{'件/日':>8}{'的中%':>8}{'表示的中%':>11}{'ROI%':>8}")
        for th in (1.40, 1.30, 1.20, 1.10):
            s2 = [r for r in sub_y if r["s"] <= th + 1e-9]
            if len(s2) < 30:
                continue
            bet = sum(r["bet"] for r in s2); pay = sum(r["pay"] for r in s2)
            hit = sum(1 for r in s2 if r["pay"] > 0)
            net = sum(1 for r in s2 if r["pay"] >= r["bet"] and r["pay"] > 0)
            print(f"  {th:>8.2f}{len(s2):>7}{len(s2)/dy:>8.2f}"
                  f"{100*hit/len(s2):>8.1f}{100*net/len(s2):>11.1f}{100*pay/bet:>8.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
