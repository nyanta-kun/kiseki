#!/usr/bin/env python3
"""9車で型ラボのプランが**商品として**成立するかを測る（2026-08-28）。

母集団は `type_lab_picks` の `mode='paper9'`（`--n-entries 9` で生成した検証行）。
🔴 9車の三連単予測オッズ `odds_tf_n9` は train_end 2024-12-31 なので
   **2025-01-01 以降だけが honest**。生成もその窓しか作っていない。

比べる相手は同じ窓の7車（`mode='paper'`）。**同じ指標・同じ定義**で並べる。
"""
from __future__ import annotations

import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from race_filter import CONFIRM, EXPLORE, WALL, boot_ci, per_day, roi, shown_hit  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

PLANS = ("A_hit", "A_pay", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_pay")


def load(mode: str, d1: str, d2: str) -> list[dict]:
    q = ("SELECT plan_key, race_date, n_legs, budget, hit, payout, final_odds, "
         "       type_label, race_type "
         "FROM type_lab_picks WHERE mode = ? AND settled_at IS NOT NULL "
         "  AND race_date BETWEEN ? AND ?")
    cols = ("plan_key", "race_date", "n_legs", "budget", "hit", "payout",
            "final_odds", "type_label", "race_type")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q, (mode, d1, d2)).fetchall()]
    for r in rows:
        r["race_date"] = str(r["race_date"])
        r["budget"] = int(r["budget"])
        r["payout"] = int(r["payout"] or 0)
    return rows


def table(rows: list[dict], title: str) -> None:
    print(f"\n== {title}  n={len(rows)}")
    if not rows:
        print("  データなし")
        return
    days = len({r["race_date"] for r in rows})
    g = defaultdict(list)
    for r in rows:
        g[r["plan_key"]].append(r)
    print(f"{'plan':8}{'n':>6}{'件/日':>7}{'点数':>6}{'表示的中':>9}"
          f"{'払戻中央':>10}{'10万+/日':>9}{'ROI':>8}  95%CI")
    for p in PLANS:
        v = g.get(p) or []
        if not v:
            continue
        hits = [r for r in v if r["hit"]]
        big = [r for r in hits if r["payout"] >= 100_000]
        med = stx.median([r["payout"] for r in v if r["payout"] > r["budget"]] or [0])
        lo, hi = boot_ci(v)
        print(f"{p:8}{len(v):6d}{len(v) / days:7.1f}"
              f"{stx.median([int(r['n_legs']) for r in v]):6.0f}"
              f"{shown_hit(v):8.2f}%{med:10,.0f}{len(big) / days:9.3f}"
              f"{roi(v):7.1f}%  [{lo:.1f}, {hi:.1f}]{'🟢' if lo > WALL else ''}")
    hits = [r for r in rows if r["hit"]]
    big = [r for r in hits if r["payout"] >= 100_000]
    med = stx.median([r["payout"] for r in rows if r["payout"] > r["budget"]] or [0])
    lo, hi = boot_ci(rows)
    print(f"{'合計':8}{len(rows):6d}{len(rows) / days:7.1f}{'':>6}"
          f"{shown_hit(rows):8.2f}%{med:10,.0f}{len(big) / days:9.3f}"
          f"{roi(rows):7.1f}%  [{lo:.1f}, {hi:.1f}]{'🟢' if lo > WALL else ''}")


def main() -> None:
    for lab, w in (("探索窓", EXPLORE), ("確認窓", CONFIRM)):
        nine = load("paper9", *w)
        seven = load("paper", *w)
        # 9車は 2026-08-04 までしか作っていないので、7車も同じ日までに揃える
        if nine:
            last = max(r["race_date"] for r in nine)
            seven = [r for r in seven if r["race_date"] <= last]
        table(nine, f"9車 {lab} {w[0]}〜{w[1]}")
        table(seven, f"7車 {lab}（同じ日まで）")

    print("\n== 型別（9車・確認窓）")
    nine = load("paper9", *CONFIRM)
    g = defaultdict(list)
    for r in nine:
        g[r["type_label"]].append(r)
    days = max(len({r["race_date"] for r in nine}), 1)
    print(f"{'型':4}{'n':>7}{'件/日':>7}{'表示的中':>9}{'払戻中央':>10}{'ROI':>8}")
    for k in "ABCDEF":
        v = g.get(k) or []
        if not v:
            continue
        med = stx.median([r["payout"] for r in v if r["payout"] > r["budget"]] or [0])
        print(f"{k:4}{len(v):7d}{len(v) / days:7.1f}{shown_hit(v):8.2f}%"
              f"{med:10,.0f}{roi(v):7.1f}%")


if __name__ == "__main__":
    main()
