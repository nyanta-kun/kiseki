"""新馬戦（能力指数が全馬中立50）は tier 内で本当に劣るのか。

「tier B なのに指数の裏付けが無い」と指摘したが、現行スコアは既にこれらを
平均13点低く採点している。同じ tier の中で比べて劣るかどうかが本当の問題。
"""
from __future__ import annotations
import argparse
import numpy as np, pandas as pd
from pathlib import Path
import sys
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
from src.indices.chihou_calculator import _scale_to_index_local  # noqa: E402
import src.indices.confidence as C  # noqa: E402

p = argparse.ArgumentParser(); p.add_argument("--csv", required=True)
a = p.parse_args(); df = pd.read_csv(a.csv)
df = df[df["finish_position"].notna()].copy()
df["won"] = (df["finish_position"] == 1).astype(int)
df["dead"] = ((df.speed_index == 50.0) & (df.last3f_index == 50.0)).astype(int)
recs = []
for rid, g in df.groupby("race_id"):
    comp = _scale_to_index_local(list(g["composite_wf"]))
    wp = sorted(list(g["win_prob_wf"]), reverse=True); s = sorted(comp, reverse=True); n = len(g)
    g12 = s[0]-s[1] if n >= 2 else 0.0
    conf = C.calculate_race_confidence(comp, n, list(g["win_prob_wf"]),
        gap_full_score=C.CHIHOU_GAP_FULL_SCORE,
        dispersion_full_score=C.CHIHOU_DISPERSION_FULL_SCORE)
    recs.append(dict(course=g["course_name"].iloc[0], score=conf["score"], rank=conf["rank"],
                     cov=1.0-g["dead"].mean(),
                     won=int(df.loc[g.index[int(np.argmax(comp))], "won"])))
R = pd.DataFrame(recs)
R["grp"] = np.where(R["cov"] < 0.001, "0%(全馬未出走)",
           np.where(R["cov"] < 0.999, "一部欠", "100%"))
print(f"walk-forward {len(R):,}R\n")
print("=== 全体（tier を無視）===")
for k, g in R.groupby("grp"):
    print(f"  {k:>16s} {len(g):>6,}R  指数1位勝率 {100*g.won.mean():5.1f}%  平均スコア {g.score.mean():5.1f}")

print("\n=== tier 内で比較（これが本当の問題）===")
print(f"{'tier':>5s} | {'全馬未出走':>22s} | {'カバレッジ100%':>22s} | 差")
for k in "SABC":
    m = R["rank"] == k
    d = R[m & (R.grp == "0%(全馬未出走)")]; f = R[m & (R.grp == "100%")]
    if len(d) < 10:
        print(f"{k:>5s} |  n={len(d):<4} 少なすぎ         |  n={len(f):<5} {100*f.won.mean():5.1f}%        | —")
        continue
    print(f"{k:>5s} |  n={len(d):<4} {100*d.won.mean():5.1f}%           |  n={len(f):<5} {100*f.won.mean():5.1f}%        | "
          f"{100*(d.won.mean()-f.won.mean()):+5.1f}pt")
