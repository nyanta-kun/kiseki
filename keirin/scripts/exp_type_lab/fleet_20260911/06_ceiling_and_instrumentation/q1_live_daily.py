#!/usr/bin/env python3
"""実入稿（type_lab_picks ∩ netkeirin_submissions）の日次「表示的中」を全期間で出す。

母集団: 実入稿・当てにいく商品（A_hit/A_trio/B_hit/C_hit/D_hit/E_hit/F_hit/F_line）
        （7車・9車の両方を含む。稼働は2026-08-27から）。
「表示的中」= payout > budget（ガミは外れ扱い）。
"""
from __future__ import annotations

import os
import numpy as np
import psycopg2

CORE_PLANS = ("A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_line")

dsn = os.environ["KEIRIN_DB_URL"]
sql = """
    SELECT p.race_date, p.plan_key, p.hit, p.payout, p.budget
    FROM keirin.type_lab_picks p
    JOIN keirin.netkeirin_submissions s
      ON s.race_key = p.race_key AND s.rank_key = p.plan_key
     AND s.deleted_at IS NULL
    WHERE p.mode LIKE 'live%%' AND p.settled_at IS NOT NULL
      AND p.plan_key IN %s
    ORDER BY p.race_date
"""
with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute(sql, (CORE_PLANS,))
    rows = cur.fetchall()

print(f"n={len(rows)}")
from collections import defaultdict
by_day = defaultdict(list)
for date, plan, hit, payout, budget in rows:
    shown = (payout or 0) > (budget or 0)
    by_day[str(date)].append(shown)

days = sorted(by_day)
n = np.array([len(by_day[d]) for d in days])
shown = np.array([sum(by_day[d]) for d in days])
rate = shown / n
baseline = shown.sum() / n.sum()
print(f"全期間基準線 = {baseline*100:.2f}%  (n={n.sum()}, {len(days)}日)")
for d, ni, si, ri in zip(days, n, shown, rate):
    print(f"  {d}  n={ni:3d}  shown={si:3d}  {ri*100:6.2f}%   Δ={100*(ri-baseline):+6.2f}pt")

# 3日移動窓
print("\n3日移動窓:")
for i in range(len(days) - 2):
    n3 = n[i:i+3].sum()
    s3 = shown[i:i+3].sum()
    print(f"  {days[i]}~{days[i+2]}  n={n3:3d}  {s3/n3*100:6.2f}%  Δ={100*(s3/n3-baseline):+6.2f}pt")
