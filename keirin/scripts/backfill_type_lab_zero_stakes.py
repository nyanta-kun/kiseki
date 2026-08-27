#!/usr/bin/env python3
"""既存の `type_lab_picks` から**賭け金 0 円の買い目**を取り除いて記録を実態に合わせる。

    python scripts/backfill_type_lab_zero_stakes.py --dry-run
    python scripts/backfill_type_lab_zero_stakes.py

## なぜ要るか

ダッチ配分では極端に高い予測オッズの点の取り分が 1 単位（100円）に満たず 0 円になる。
`prob_top` が Σ(1/予測オッズ) の枠を超えた候補を `break` せず `continue` するため、
**B_hit の 61.7%（実測 3,054行）** がこれを含んでいた。

    n_legs が実際に買う点数と違う / pred_min_payout の中央値が 0円 /
    pred_mean_payout が設計の床（3万円）を下回る（中央 26,461円）

`src/type_lab.allocate` は 2026-08-28 から 0 円の点を返さない。この script は
**既に保存済みの行**を同じ状態へ揃える。

## 🔴 モデルを回さない

行に保存済みの `legs[].pred_odds` / `prob` から `allocate` を呼び直すだけ。
予測をやり直さないので**過去の行の意味が変わらない**（20か月ぶんを作り直すと
数時間かかるうえ、その時点のモデルを再現できない）。

## 🔴 採点結果には触れない

`hit` / `payout` / `settled_at` / `win_combo` は更新しない。0 円の点は当たっても
払戻 0 なので**採点は元から正しい**（実測でも 0円の点が当たり目だった行は 0件）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.type_lab import (                          # noqa: E402
    PLANS, allocate, mean_expected_payout, min_expected_payout,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with get_connection() as c:
        rows = c.execute(
            "SELECT id, mode, plan_key, bet_type, legs FROM type_lab_picks").fetchall()
    todo, skipped = [], 0
    for _id, mode, plan_key, bet_type, legs_raw in rows:
        legs = json.loads(legs_raw) if isinstance(legs_raw, str) else legs_raw
        if not legs or all(int(l.get("stake", 0)) > 0 for l in legs):
            continue
        plan = PLANS.get(plan_key)
        if plan is None:
            skipped += 1
            continue
        keep = [l for l in legs if int(l.get("stake", 0)) > 0]
        if not keep:
            skipped += 1
            continue
        # 保存済みの予測から配り直す（モデルは回さない）
        keys = [l["combo"] for l in keep]
        odds = {l["combo"]: float(l["pred_odds"]) for l in keep}
        prob = {l["combo"]: float(l.get("prob") or 0.0) for l in keep}
        stakes = allocate(keys, odds, prob, plan)
        if not stakes:
            skipped += 1
            continue
        detail = [{"combo": k, "stake": int(stakes[k]),
                   "pred_odds": round(odds[k], 2), "prob": round(prob[k], 6)}
                  for k in keys if k in stakes]
        todo.append((json.dumps(detail, ensure_ascii=False), len(detail),
                     round(mean_expected_payout(stakes, odds), 1),
                     round(min_expected_payout(stakes, odds), 1), _id,
                     mode, plan_key, len(legs)))

    print(f"対象 {len(todo):,} 行 / 組み直せなかった {skipped:,} 行")
    if todo:
        s = todo[0]
        print(f"  例: id={s[4]} {s[5]}/{s[6]}  {s[7]}点 → {s[1]}点  "
              f"想定平均 {s[2]:,.0f}円 / 想定最低 {s[3]:,.0f}円")
    if a.dry_run:
        print("[dry-run] 保存しない")
        return
    with get_connection() as c:
        c.executemany(
            "UPDATE type_lab_picks SET legs = ?, n_legs = ?, pred_mean_payout = ?, "
            "pred_min_payout = ? WHERE id = ?", [t[:5] for t in todo])
        c.commit()
    print(f"更新 {len(todo):,} 行")


if __name__ == "__main__":
    main()
