"""配点を大きく振り直した場合に wp_sum が tier 分離を改善するかを確認する。

15点枠の組み替えでは +1pt しか動かなかった。頭数スコア(20点)は弱い信号なので
そこからも移すことを含め、広く掃く。あわせて「tier を wp_sum で二分する」
第2軸案（配点をいじらない）とも比較する。
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
df = pd.read_csv(a.csv); df = df[df["finish_position"].notna()].copy()
df["won"] = (df["finish_position"] == 1).astype(int)
recs = []
for rid, g in df.groupby("race_id"):
    comp = _scale_to_index_local(list(g["composite_wf"]))
    wp = sorted(list(g["win_prob_wf"]), reverse=True); s = sorted(comp, reverse=True); n = len(g)
    gap12 = s[0]-s[1] if n >= 2 else 0.0
    recs.append(dict(course=g["course_name"].iloc[0], n=n,
                     wgap=gap12*0.7 + ((s[0]-s[2]) if n >= 3 else gap12)*0.3,
                     sd=float(np.std(s, ddof=1)) if n >= 2 else 0.0,
                     pgap=wp[0]-wp[1] if n >= 2 else 0.0, ptop=wp[0],
                     wp_sum=float(np.sum(wp)),
                     won=int(df.loc[g.index[int(np.argmax(comp))], "won"])))
R = pd.DataFrame(recs); print(f"walk-forward {len(R):,}R\n")

def parts(R):
    return (np.minimum(R.wgap/C.CHIHOU_GAP_FULL_SCORE,1.0)*40.0,
            np.maximum(0.0,(18-R.n)/10.0)*20.0,
            np.minimum(R.sd/C.CHIHOU_DISPERSION_FULL_SCORE,1.0)*25.0,
            np.minimum(R.pgap/0.20,1.0)*15.0)
GS, HS, DS, WS0 = parts(R)
WS0 = np.where(R.ptop >= 0.40, np.minimum(WS0+5.0, 15.0), WS0)

def rank(s): return np.select([s>=80, s>=65, s>=50], ["S","A","B"], "C")
def rep(s, label, R=R):
    r = rank(s); o = {k: 100*R.won[r==k].mean() if (r==k).sum() else np.nan for k in "SABC"}
    d = {k: 100*(r==k).mean() for k in "SABC"}
    mono = o["S"]>o["A"]>o["B"]>o["C"]
    print(f"{label:36s} S{d['S']:5.1f}%/{o['S']:5.1f}  A{d['A']:5.1f}%/{o['A']:5.1f}  "
          f"B{d['B']:5.1f}%/{o['B']:5.1f}  C{d['C']:5.1f}%/{o['C']:5.1f}  S-C{o['S']-o['C']:5.1f}pt"
          f"{'' if mono else ' ✗'}")
    return o["S"]-o["C"], mono

print("           tier分布%/指数1位勝率")
rep(np.clip(np.round(GS+HS+DS+WS0),0,100), "現行")
print()
best=[]
# 頭数スコアからも移す: head 20 → (20-Q), wp_sum に P+Q 点
for P, Q, lo, hi in itertools.product([9,15],[0,10,20],[0.75,0.85],[1.10,1.20]):
    sumpart = np.clip((R.wp_sum-lo)/(hi-lo),0,1)*(P+Q)
    gpart = np.minimum(R.pgap/0.20,1.0)*(15.0-P)
    hs = np.maximum(0.0,(18-R.n)/10.0)*(20.0-Q)
    s = np.clip(np.round(GS+hs+DS+gpart+sumpart),0,100)
    sep, mono = rep(s, f"P={P} Q={Q} lo={lo} hi={hi}")
    if mono: best.append((sep,P,Q,lo,hi))
best.sort(reverse=True)
print(f"\n最良: {best[0] if best else 'なし'}")

# ── 第2軸案: tier はそのまま、wp_sum 中央値で二分する ──
print("\n=== 第2軸案（配点は一切変えず tier を wp_sum 中央値で二分）===")
s = np.clip(np.round(GS+HS+DS+WS0),0,100); r = rank(s)
med = R.wp_sum.median()
print(f"  (全場の合計中央値 = {med:.3f})")
for k in "SABC":
    m = r == k
    if m.sum() < 50: continue
    hi_ = m & (R.wp_sum >= med); lo_ = m & (R.wp_sum < med)
    print(f"  tier {k}: 全体 {100*R.won[m].mean():5.1f}%  →  "
          f"合計↑ {100*R.won[hi_].mean():5.1f}% (n={hi_.sum():,})  /  "
          f"合計↓ {100*R.won[lo_].mean():5.1f}% (n={lo_.sum():,})  "
          f"差{100*(R.won[hi_].mean()-R.won[lo_].mean()):+5.1f}pt")
