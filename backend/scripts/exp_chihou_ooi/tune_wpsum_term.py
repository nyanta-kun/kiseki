"""勝率集中スコア15点の組み替えを walk-forward データで決める。

現行: min(prob_gap/0.20, 1)*15  (+ top>=0.40 で +5, 上限15)
案  : min(prob_gap/0.20, 1)*(15-P) + clip((wp_sum-LO)/(HI-LO),0,1)*P
評価: tier の単調性・S/A/B/C の分離幅・tier 分布（現行から大きく動かさない）
"""
from __future__ import annotations
import argparse, itertools
import numpy as np, pandas as pd
from pathlib import Path
import sys
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
from src.indices.chihou_calculator import _scale_to_index_local  # noqa: E402
import src.indices.confidence as C  # noqa: E402

p = argparse.ArgumentParser(); p.add_argument("--csv", required=True)
a = p.parse_args()
df = pd.read_csv(a.csv)
df = df[df["finish_position"].notna()].copy()
df["won"] = (df["finish_position"] == 1).astype(int)
df["top3lbl"] = (df["finish_position"] <= 3).astype(int)

# レース単位の素材を一度だけ作る
recs = []
for rid, g in df.groupby("race_id"):
    comp = _scale_to_index_local(list(g["composite_wf"]))
    wp = sorted(list(g["win_prob_wf"]), reverse=True)
    s = sorted(comp, reverse=True)
    n = len(g)
    gap12 = s[0]-s[1] if n >= 2 else 0.0
    gap13 = s[0]-s[2] if n >= 3 else gap12
    top = g.iloc[int(np.argmax(comp))]
    recs.append(dict(race_id=rid, course=g["course_name"].iloc[0], n=n,
                     wgap=gap12*0.7+gap13*0.3,
                     sd=float(np.std(s, ddof=1)) if n >= 2 else 0.0,
                     pgap=wp[0]-wp[1] if n >= 2 else 0.0, ptop=wp[0],
                     wp_sum=float(np.sum(wp)),
                     won=int(top["won"]), top3=int(top["top3lbl"])))
R = pd.DataFrame(recs)
print(f"walk-forward {len(R):,}R\n")

def score(R, sum_pts, lo, hi):
    gs = np.minimum(R.wgap / C.CHIHOU_GAP_FULL_SCORE, 1.0) * 40.0
    hs = np.maximum(0.0, (18 - R.n) / 10.0) * 20.0
    ds = np.minimum(R.sd / C.CHIHOU_DISPERSION_FULL_SCORE, 1.0) * 25.0
    if sum_pts == 0:
        ws = np.minimum(R.pgap / 0.20, 1.0) * 15.0
        ws = np.where(R.ptop >= 0.40, np.minimum(ws + 5.0, 15.0), ws)
    else:
        gpart = np.minimum(R.pgap / 0.20, 1.0) * (15.0 - sum_pts)
        spart = np.clip((R.wp_sum - lo) / (hi - lo), 0, 1) * sum_pts
        ws = gpart + spart
        ws = np.where(R.ptop >= 0.40, np.minimum(ws + 5.0, 15.0), ws)
    return np.clip(np.round(gs + hs + ds + ws), 0, 100)

def rank(s):
    return np.select([s >= 80, s >= 65, s >= 50], ["S", "A", "B"], "C")

def report(R, s, label):
    r = rank(s)
    out, dist = {}, {}
    for k in "SABC":
        m = r == k
        dist[k] = 100*m.mean()
        out[k] = 100*R.won[m].mean() if m.sum() else float("nan")
    mono = out["S"] > out["A"] > out["B"] > out["C"]
    print(f"{label:34s} S{dist['S']:5.1f}% A{dist['A']:5.1f}% B{dist['B']:5.1f}% C{dist['C']:5.1f}% | "
          f"勝率 S{out['S']:5.1f} A{out['A']:5.1f} B{out['B']:5.1f} C{out['C']:5.1f} | "
          f"S-C {out['S']-out['C']:5.1f}pt {'単調' if mono else '✗非単調'}")
    return out["S"]-out["C"], mono, dist

print(f"{'':34s} {'tier分布':^28s} | {'指数1位勝率':^28s} | 分離")
base = report(R, score(R, 0, 0, 0), "現行")
print()
best = []
for sum_pts, lo, hi in itertools.product([5, 7, 9, 11], [0.75, 0.80, 0.85], [1.10, 1.15, 1.20]):
    sep, mono, dist = report(R, score(R, sum_pts, lo, hi), f"P={sum_pts} lo={lo} hi={hi}")
    if mono: best.append((sep, sum_pts, lo, hi, dist))
best.sort(reverse=True)
print("\n=== 分離幅トップ5（単調なもののみ）===")
for sep, sp, lo, hi, dist in best[:5]:
    print(f"  P={sp} lo={lo} hi={hi}: S-C {sep:.1f}pt  S{dist['S']:.1f}% A{dist['A']:.1f}%")
