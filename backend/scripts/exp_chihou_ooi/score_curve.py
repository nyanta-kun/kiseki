"""現行スコアと新スコアを「選択率を揃えて」比較する。

tier 分布が変わる案どうしを S-C 幅で比べると、選択的な案が自動的に有利になる。
「上位X%のレースを選んだときの指数1位勝率」で比べるのが公平。
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
a = p.parse_args()
df = pd.read_csv(a.csv); df = df[df["finish_position"].notna()].copy()
df["won"] = (df["finish_position"] == 1).astype(int)
recs = []
for rid, g in df.groupby("race_id"):
    comp = _scale_to_index_local(list(g["composite_wf"]))
    wp = sorted(list(g["win_prob_wf"]), reverse=True); s = sorted(comp, reverse=True); n = len(g)
    g12 = s[0]-s[1] if n >= 2 else 0.0
    recs.append(dict(course=g["course_name"].iloc[0], n=n,
                     wgap=g12*0.7 + ((s[0]-s[2]) if n >= 3 else g12)*0.3,
                     sd=float(np.std(s, ddof=1)) if n >= 2 else 0.0,
                     pgap=wp[0]-wp[1] if n >= 2 else 0.0, ptop=wp[0],
                     wp_sum=float(np.sum(wp)),
                     won=int(df.loc[g.index[int(np.argmax(comp))], "won"])))
R = pd.DataFrame(recs)

GS = np.minimum(R.wgap/C.CHIHOU_GAP_FULL_SCORE,1.0)*40.0
HS = np.maximum(0.0,(18-R.n)/10.0)*20.0
DS = np.minimum(R.sd/C.CHIHOU_DISPERSION_FULL_SCORE,1.0)*25.0
WS_cur = np.where(R.ptop>=0.40, np.minimum(np.minimum(R.pgap/0.20,1.0)*15.0+5.0,15.0),
                  np.minimum(R.pgap/0.20,1.0)*15.0)
WS_new = np.clip((R.wp_sum-0.85)/(1.20-0.85),0,1)*15.0

R["cur"] = GS+HS+DS+WS_cur
R["new"] = GS+HS+DS+WS_new

def curve(sub, label):
    print(f"\n--- {label}  ({len(sub):,}R) ---")
    print(f"{'上位':>6s} {'現行':>8s} {'新(wp_sum)':>11s} {'差':>8s}")
    for frac in [0.05, 0.10, 0.20, 0.30, 0.40, 0.50]:
        k = max(1, int(len(sub)*frac))
        c = sub.nlargest(k, "cur").won.mean()*100
        nw = sub.nlargest(k, "new").won.mean()*100
        print(f"{100*frac:5.0f}% {c:7.1f}% {nw:10.1f}% {nw-c:+7.1f}pt")

curve(R, "全場")
curve(R[R.course=="大井"], "大井")

# bootstrap: 上位20%での差
rng = np.random.default_rng(0)
def boot(sub, frac=0.20, nb=2000):
    k = max(1, int(len(sub)*frac))
    d = []
    for _ in range(nb):
        s = sub.sample(len(sub), replace=True, random_state=int(rng.integers(1e9)))
        d.append(s.nlargest(k,"new").won.mean() - s.nlargest(k,"cur").won.mean())
    return np.percentile(d,[2.5,97.5])
lo,hi = boot(R)
print(f"\n全場 上位20% の差: 95%CI [{100*lo:+.1f}, {100*hi:+.1f}]pt")
lo,hi = boot(R[R.course=="大井"])
print(f"大井 上位20% の差: 95%CI [{100*lo:+.1f}, {100*hi:+.1f}]pt")

# ── 第2軸案の検定: tier 内で wp_sum は「連続スコア」を超える情報を持つか ──
print("\n=== tier 内分割: wp_sum で割る vs 連続スコアで割る（同じ tier 内）===")
R["rank_cur"] = np.select([R.cur>=80, R.cur>=65, R.cur>=50], ["S","A","B"], "C")
print(f"{'tier':>5s} {'n':>6s} {'全体':>7s} | {'wp_sum↑':>8s} {'wp_sum↓':>8s} {'差':>7s} | "
      f"{'score↑':>8s} {'score↓':>8s} {'差':>7s}")
for k in "SABC":
    m = R.rank_cur == k
    if m.sum() < 200: continue
    sub = R[m]
    a1 = sub[sub.wp_sum >= sub.wp_sum.median()].won.mean()*100
    a2 = sub[sub.wp_sum <  sub.wp_sum.median()].won.mean()*100
    b1 = sub[sub.cur    >= sub.cur.median()].won.mean()*100
    b2 = sub[sub.cur    <  sub.cur.median()].won.mean()*100
    print(f"{k:>5s} {len(sub):>6,} {sub.won.mean()*100:6.1f}% | {a1:7.1f}% {a2:7.1f}% {a1-a2:+6.1f}pt | "
          f"{b1:7.1f}% {b2:7.1f}% {b1-b2:+6.1f}pt")
