#!/usr/bin/env python3
"""「軸が揃わない日」は朝に分かるか（2026-08-25）。

分かるなら日ごとに件数や商品を変えられる。分からないなら日々のムラは
**取り除けない分散**であり、設計で消そうとしてはいけない。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import os
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)
from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402


def load(p):
    out = []
    for x in open(p):
        r = json.loads(x)
        p3 = {int(k): v for k, v in r["p3"].items()}
        out.append((r["race_key"], r["race_date"], r["order"], p3))
    return out


rows = load("data/exp/trio7_cache_wf_train.jsonl") + \
       load("data/exp/trio7_cache_wf_test.jsonl")
fins = _load_finishes([r[0] for r in rows])
by = defaultdict(list)
for key, date, o, p3 in rows:
    f = fins.get(key)
    if not f or len(o) < 7:
        continue
    top3 = {c for w in winning_trifectas(f) for c in w}
    by[date].append((p3[o[0]] + p3[o[1]], int(o[0] in top3 and o[1] in top3),
                     int(o[0] in top3)))

d = [(k, np.array(v, float)) for k, v in by.items() if len(v) >= 20]
X = np.array([v[:, 0].mean() for _, v in d])          # 朝に分かる: 軸2車のp3合計の日平均
Y = np.array([v[:, 1].mean() for _, v in d])          # 事後: 二軸そろい率
Z = np.array([v[:, 2].mean() for _, v in d])          # 事後: ◎の3着内率
n = np.array([len(v) for _, v in d])
print(f"日数 {len(d):,}  1日平均 {n.mean():.1f}R")
print(f"\n二軸そろい率の日次分布: 平均 {Y.mean():.1%}  sd {Y.std():.1%}  "
      f"10%点 {np.percentile(Y,10):.1%}  90%点 {np.percentile(Y,90):.1%}")
print(f"◎の3着内率  の日次分布: 平均 {Z.mean():.1%}  sd {Z.std():.1%}  "
      f"10%点 {np.percentile(Z,10):.1%}  90%点 {np.percentile(Z,90):.1%}")
print(f"\n朝に分かる量（軸2車のp3合計・日平均）との相関:")
print(f"  vs 二軸そろい率   r = {np.corrcoef(X,Y)[0,1]:+.3f}")
print(f"  vs ◎の3着内率     r = {np.corrcoef(X,Z)[0,1]:+.3f}")

# 二項分布から期待されるばらつき（＝完全にランダムなら幾らか）
p = Y.mean()
exp_sd = float(np.sqrt(p * (1 - p) / n.mean()))
print(f"\n二項ゆらぎだけで説明される sd = {exp_sd:.1%}  実測 sd = {Y.std():.1%}"
      f"  → 超過分 {max(Y.std()**2-exp_sd**2,0)**.5:.1%}")
q = np.quantile(X, [0, .2, .4, .6, .8, 1.0])
print(f"\n【朝の指標で日を5分位に切ったときの実際の二軸そろい率】")
for i in range(5):
    m = (X >= q[i]) & (X <= q[i + 1])
    print(f"  Q{i+1}  日数{m.sum():>4}  朝の平均p3和 {X[m].mean():.3f}"
          f"  → 二軸そろい {Y[m].mean():.2%}  ◎3着内 {Z[m].mean():.2%}")
