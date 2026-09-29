"""E_hit: 帯30倍の下で決着するレースを事前に見分け、そこだけ帯を下げて救えるか（2026-09-29）。"""
import pickle
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
R = [r for r in pickle.load(open(HERE / "ae_rows.pkl", "rb")) if r["used"] == "E_hit"]
ND = {"探索": 549, "確認": 216}
def res(r, a): return (r["inv"], r["pay"], r["n"]) if a == "cur" else (r["arms"][a] or (r["inv"], r["pay"], r["n"]))
def hit(v): return v[1] > v[0]
def auc(x, y):
    x = np.asarray(x, float); y = np.asarray(y, bool)
    o = np.argsort(x, kind="mergesort"); rk = np.empty(len(x)); rk[o] = np.arange(1, len(x)+1)
    # 同順位は平均順位
    _, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    s = np.bincount(inv, rk) / cnt; rk = s[inv]
    n1 = y.sum(); n0 = len(y) - n1
    return (rk[y].sum() - n1*(n1+1)/2) / (n1*n0)
ex = [r for r in R if r["win"] == "探索"]; cf = [r for r in R if r["win"] == "確認"]
def yb(r): return r["n_ax"] == 2 and np.isfinite(r["win_po"]) and r["win_po"] < 30 and not r["pay"] > r["inv"]
print(f"E_hit n 探索{len(ex)} 確認{len(cf)}  帯下決着(2軸∧30倍未満で外れ) {100*np.mean([yb(r) for r in ex]):.1f}% / {100*np.mean([yb(r) for r in cf]):.1f}%")
print(f"  現行の的中 {100*np.mean([r['pay']>r['inv'] for r in ex]):.1f}% / {100*np.mean([r['pay']>r['inv'] for r in cf]):.1f}%")
feats = list(ex[0]["f"])
print("\n[1] 帯下決着を事前に分ける力（AUC 探索 / 確認・0.5 から離れるほど効く）")
for k in feats:
    a1 = auc([r["f"][k] for r in ex], [yb(r) for r in ex]); a2 = auc([r["f"][k] for r in cf], [yb(r) for r in cf])
    print(f"  {k:<14}{a1:6.3f} {a2:6.3f}")
# 全面の代替（条件なし）
print("\n[2] 条件なしで全 E_hit を代替腕にした場合（救済/破壊・的中・ROI）")
for w, rs in (("探索", ex), ("確認", cf)):
    for a in rs[0]["arms"]:
        av = np.mean([r["arms"][a] is not None for r in rs])
        resc = sum(hit(res(r, a)) and not hit(res(r, "cur")) for r in rs); brk = sum(hit(res(r, "cur")) and not hit(res(r, a)) for r in rs)
        v = [res(r, a) for r in rs]; h = [p for i, p, _ in v if p > i]
        print(f"  {w} {a:<5} 組成{100*av:4.0f}% 救済{resc:>4} 破壊{brk:>4} 的中{100*len(h)/len(v):5.1f} ROI{100*sum(p for _,p,_ in v)/sum(i for i,_,_ in v):6.1f} 払戻中央{statistics.median(h):>8,.0f} 1万+/日{sum(p>=10000 for p in h)/ND[w]:.2f} 3万+/日{sum(p>=30000 for p in h)/ND[w]:.2f}")
# セル探索: 特徴の上位/下位 1/3（探索窓の分位）× 代替腕
print("\n[3] 条件つき（探索窓で選び、確認窓で読む）。探索で Δ的中 CI 下限>0 かつ 救済>破壊×2 のセルのみ ★")
def boot_d(rs, a, B=500):
    days = defaultdict(list)
    for j, r in enumerate(rs): days[r["day"]].append(j)
    ks = list(days); rng = np.random.default_rng(0); d = []
    for _ in range(B):
        idx = [j for k in rng.integers(0, len(ks), len(ks)) for j in days[ks[k]]]
        d.append(100*(np.mean([hit(res(rs[j], a)) for j in idx]) - np.mean([hit(res(rs[j], "cur")) for j in idx])))
    return np.percentile(d, 2.5), np.percentile(d, 97.5)
cells = []
for k in feats:
    vals = [r["f"][k] for r in ex]
    lo, hi = np.quantile(vals, [1/3, 2/3])
    for side, cond in (("下1/3", lambda v, lo=lo: v <= lo), ("上1/3", lambda v, hi=hi: v >= hi)):
        if lo == hi and side == "下1/3" and len(set(vals)) <= 3: cond = (lambda v, lo=lo: v <= lo)
        for a in ex[0]["arms"]:
            e = [r for r in ex if cond(r["f"][k])]
            if len(e) < 80 or len(e) > 0.8*len(ex): continue
            re_ = sum(hit(res(r, a)) and not hit(res(r, "cur")) for r in e); be = sum(hit(res(r, "cur")) and not hit(res(r, a)) for r in e)
            de = 100*(np.mean([hit(res(r, a)) for r in e]) - np.mean([hit(res(r, "cur")) for r in e]))
            cells.append((de, k, side, a, cond, len(e), re_, be))
cells.sort(key=lambda t: -t[0])
chosen = []
for de, k, side, a, cond, ne, re_, be in cells[:25]:
    e = [r for r in ex if cond(r["f"][k])]
    ci = boot_d(e, a)
    c = [r for r in cf if cond(r["f"][k])]
    rc = sum(hit(res(r, a)) and not hit(res(r, "cur")) for r in c); bc = sum(hit(res(r, "cur")) and not hit(res(r, a)) for r in c)
    dc = 100*(np.mean([hit(res(r, a)) for r in c]) - np.mean([hit(res(r, "cur")) for r in c])) if c else float("nan")
    roi_e = lambda rs, aa: 100*sum(res(r, aa)[1] for r in rs)/sum(res(r, aa)[0] for r in rs)
    star = ci[0] > 0 and re_ > 2*be
    if star: chosen.append((k, side, a, cond))
    print(f"  {k:<13}{side} {a:<5} 探索 n{ne:>4} Δ{de:+5.1f} [{ci[0]:+.1f},{ci[1]:+.1f}] 救{re_}/壊{be} ROI {roi_e(e,'cur'):.0f}→{roi_e(e,a):.0f} | 確認 n{len(c):>4} Δ{dc:+5.1f} 救{rc}/壊{bc} ROI {roi_e(c,'cur'):.0f}→{roi_e(c,a):.0f} {'★' if star else ''}")
pickle.dump([(k, s, a) for k, s, a, _ in chosen], open(HERE / "ae_e_chosen.pkl", "wb"))
