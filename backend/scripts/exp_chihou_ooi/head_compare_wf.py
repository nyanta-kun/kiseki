"""walk-forward(honest) 予測で top3ヘッド と winヘッド のランキング品質を比較する。

表示順は composite（is_top3ヘッド）だが、勝ち馬を当てるなら is_win ヘッドの方が
良いのではないか、を in-sample でない予測で確かめる。
入力は chihou_rebuild_walkforward.py --dump-csv の出力。
"""
from __future__ import annotations
import argparse, sys
import numpy as np, pandas as pd

p = argparse.ArgumentParser(); p.add_argument("--csv", required=True)
a = p.parse_args()
df = pd.read_csv(a.csv)
df = df[df["finish_position"].notna()].copy()
df["won"] = (df["finish_position"] == 1).astype(int)
df["top3"] = (df["finish_position"] <= 3).astype(int)

g = df.groupby("race_id")
df["c_rank"] = g["composite_wf"].rank(ascending=False, method="first")
df["w_rank"] = g["win_prob_wf"].rank(ascending=False, method="first")

def blk(sub, label):
    c1 = sub[sub.c_rank == 1]; w1 = sub[sub.w_rank == 1]
    if len(c1) < 30: return None
    agree = (sub[sub.c_rank == 1].set_index("race_id")["w_rank"] == 1).mean()
    return dict(label=label, n=len(c1),
                comp_win=100*c1.won.mean(), win_win=100*w1.won.mean(),
                comp_top3=100*c1.top3.mean(), win_top3=100*w1.top3.mean(),
                agree=100*agree)

print(f"walk-forward honest: {df['race_id'].nunique():,}R / {len(df):,}行\n")
rows = [blk(df, "全体")]
for q, s in df.groupby("quarter"): rows.append(blk(s, f"  {q}"))
rows = [r for r in rows if r]
print(f"{'':26s} {'R数':>6s} {'top3ヘッド':>9s} {'winヘッド':>9s} {'差':>7s} {'1位一致':>8s}")
for r in rows:
    print(f"{r['label']:26s} {r['n']:>6,} {r['comp_win']:>8.1f}% {r['win_win']:>8.1f}% "
          f"{r['win_win']-r['comp_win']:>+6.1f}pt {r['agree']:>7.1f}%")

print(f"\n=== 場別（全四半期プール）===")
print(f"{'場':>8s} {'R数':>6s} {'top3ヘッド':>9s} {'winヘッド':>9s} {'差':>7s}")
out = []
for c, s in df.groupby("course_name"):
    r = blk(s, c)
    if r: out.append(r)
for r in sorted(out, key=lambda x: -(x['win_win']-x['comp_win'])):
    mark = "  ★" if r['label'] == "大井" else ""
    print(f"{r['label']:>8s} {r['n']:>6,} {r['comp_win']:>8.1f}% {r['win_win']:>8.1f}% "
          f"{r['win_win']-r['comp_win']:>+6.1f}pt{mark}")

# レース単位 paired bootstrap（全体・大井）
def boot(sub, label, n_boot=2000, seed=0):
    rng = np.random.default_rng(seed)
    ids = sub.race_id.unique()
    c = sub[sub.c_rank == 1].set_index("race_id")["won"]
    w = sub[sub.w_rank == 1].set_index("race_id")["won"]
    common = c.index.intersection(w.index)
    c, w = c.loc[common].to_numpy(), w.loc[common].to_numpy()
    d = np.array([ (w[i]-c[i]).mean() for i in
                   (rng.integers(0, len(c), len(c)) for _ in range(n_boot)) ])
    lo, hi = np.percentile(d, [2.5, 97.5])
    print(f"  {label:>8s}: 差 {100*(w.mean()-c.mean()):+.2f}pt  95%CI [{100*lo:+.2f}, {100*hi:+.2f}]  n={len(c):,}R")

print("\n=== レース単位 paired bootstrap（win − top3, 指数1位馬の勝率）===")
boot(df, "全体")
boot(df[df.course_name == "大井"], "大井")
