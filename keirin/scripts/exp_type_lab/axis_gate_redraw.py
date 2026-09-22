#!/usr/bin/env python3
"""軸信頼ゲートの閾値を**いまの商品で**引き直す（2026-09-23）。

`AXIS_GATE_MIN` は旧商品（信頼度傾斜の確率上位）で引いた**プラン内 p30**。
2026-09-22 に堅い3型がダッチへ移ったので、同じ絶対値が別の分位を意味している。

引き方は原典（`axis_gate_scope2.py`）と同じ:
  ① そのレースで**売るプラン**を決める（`sell_plans_for`）
  ② **入稿ゲートを先に当てる**（通った行だけ）
  ③ プランごとに `axis_sum` の分位を取る

🔴 **探索窓で引いて確認窓で評価する**（同じ窓で引いて同じ窓で測らない）。
"""
from __future__ import annotations
import argparse, statistics, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lineup_sim import board, ctx, build, gate_ok                    # noqa: E402
import lineup_arms as R                                              # noqa: E402
from src.type_lab import PLANS                                       # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="2024-07-01")
ap.add_argument("--end", default="2025-12-31")
ap.add_argument("--quantiles", default="10,20,30,40")
a = ap.parse_args()

z = board()
m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"])
     & z["OKPRED"] & (z["DATE"] >= a.start) & (z["DATE"] <= a.end))
idx = np.flatnonzero(m)
vals: dict[str, list[float]] = defaultdict(list)
for i in idx:
    x = ctx(int(i))
    if x is None:
        continue
    main, _ = R.race_rows(x, {})
    if main is None:            # 入稿ゲートを通らない行は母集団に入れない
        continue
    vals[main["plan"]].append(float(x.shape.axis_sum))

qs = [int(q) for q in a.quantiles.split(",")]
print(f"=== `axis_sum` のプラン内分位（{a.start}〜{a.end}・入稿ゲート通過後） ===")
head = f"{'プラン':<9}{'件':>7}" + "".join(f"{'p'+str(q):>9}" for q in qs) + f"{'現行':>9}{'現行の分位':>11}"
print(head); print("-" * len(head))
out = {}
for k in sorted(vals):
    v = sorted(vals[k])
    if len(v) < 200:
        continue
    row = f"{k:<9}{len(v):>7}"
    out[k] = {}
    for q in qs:
        t = v[int(len(v) * q / 100)]
        out[k][q] = t
        row += f"{t:>9.3f}"
    cur = R._G.AXIS_GATE_MIN.get(k)
    if cur is None:
        row += f"{'—':>9}{'—':>11}"
    else:
        pct = sum(1 for x_ in v if x_ < cur) / len(v) * 100
        row += f"{cur:>9.3f}{f'p{pct:.0f}':>11}"
    print(row)
print("\n引き直した表（貼り付け用）")
for q in qs:
    print(f"  p{q}: " + "{" + ", ".join(f'"{k}": {out[k][q]:.3f}' for k in sorted(out)) + "}")
