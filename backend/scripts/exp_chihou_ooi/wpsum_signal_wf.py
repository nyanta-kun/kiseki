"""B の根拠（win_probability 合計＝レース難度信号）を walk-forward で再検証する。

in-sample の DB 値では合計と指数1位勝率に強い関係が出たが、C が in-sample で
大きく水増しされていたため、同じ検証を honest 予測でやり直す。
"""
from __future__ import annotations
import argparse
import numpy as np, pandas as pd
from pathlib import Path
import sys
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
from src.indices.chihou_calculator import _scale_to_index_local  # noqa: E402
from src.indices.confidence import (  # noqa: E402
    calculate_race_confidence, CHIHOU_GAP_FULL_SCORE, CHIHOU_DISPERSION_FULL_SCORE)

p = argparse.ArgumentParser(); p.add_argument("--csv", required=True)
a = p.parse_args()
df = pd.read_csv(a.csv)
df = df[df["finish_position"].notna()].copy()
df["won"] = (df["finish_position"] == 1).astype(int)

rows = []
for rid, g in df.groupby("race_id"):
    comp = _scale_to_index_local(list(g["composite_wf"]))
    wp = list(g["win_prob_wf"])
    conf = calculate_race_confidence(comp, len(g), wp,
        gap_full_score=CHIHOU_GAP_FULL_SCORE,
        dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE)
    top = g.iloc[int(np.argmax(comp))]
    rows.append({"race_id": rid, "course": g["course_name"].iloc[0],
                 "score": conf["score"], "rank": conf["rank"],
                 "wp_sum": float(np.sum(wp)), "hc": len(g), "idx1_won": int(top["won"])})
R = pd.DataFrame(rows)
print(f"walk-forward honest {len(R):,}R\n")

print("=== wp_sum 5分位ごとの指数1位勝率 ===")
print(f"{'':>16s} {'R数':>6s} {'合計中央':>8s} {'頭数':>5s} {'指数1位勝率':>10s}")
for lbl, sub in [("全場", R), ("大井", R[R.course == "大井"])]:
    s = sub.copy(); s["q"] = pd.qcut(s.wp_sum, 5, labels=["1:最小","2","3","4","5:最大"])
    for k, g in s.groupby("q", observed=True):
        print(f"{lbl:>6s} {str(k):>9s} {len(g):>6,} {g.wp_sum.median():>8.3f} "
              f"{g.hc.mean():>5.1f} {100*g.idx1_won.mean():>9.1f}%")
    print()

print("=== 現行スコア層の中での残差予測力（これが B の核心）===")
R["sband"] = pd.cut(R.score, [-1, 35, 50, 65, 101], labels=["~35","36-50","51-65","66+"])
print(f"{'スコア帯':>8s} {'R数':>6s} {'wp_sum下1/3':>12s} {'中1/3':>8s} {'上1/3':>8s} {'差':>8s}")
for k, g in R.groupby("sband", observed=True):
    if len(g) < 200: continue
    t = pd.qcut(g.wp_sum, 3, labels=[0,1,2])
    v = [100*g[t==i].idx1_won.mean() for i in range(3)]
    print(f"{str(k):>8s} {len(g):>6,} {v[0]:>11.1f}% {v[1]:>7.1f}% {v[2]:>7.1f}% {v[2]-v[0]:>+7.1f}pt")

# bootstrap で 66+ 帯の差に CI を付ける
g = R[R.sband == "66+"].copy()
t = pd.qcut(g.wp_sum, 3, labels=[0,1,2])
lo_, hi_ = g[t==0].idx1_won.to_numpy(), g[t==2].idx1_won.to_numpy()
rng = np.random.default_rng(0)
d = [hi_[rng.integers(0,len(hi_),len(hi_))].mean() - lo_[rng.integers(0,len(lo_),len(lo_))].mean()
     for _ in range(2000)]
print(f"\n66+帯 上1/3 − 下1/3: {100*(hi_.mean()-lo_.mean()):+.1f}pt  "
      f"95%CI [{100*np.percentile(d,2.5):+.1f}, {100*np.percentile(d,97.5):+.1f}]")
