"""A_hit: 安い配当のレースを「見送る」か「払戻を下げて取る」か（2026-09-29）。"""
import pickle
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
R = [r for r in pickle.load(open(HERE / "ae_rows.pkl", "rb")) if r["used"] == "A_hit"]
ND = {"探索": 549, "確認": 216}
def res(r, act):
    if act == "skip": return None
    if act == "cur": return (r["inv"], r["pay"], r["n"])
    return r["arms"][act] or (r["inv"], r["pay"], r["n"])
def met(rs, acts, w):
    v = [res(r, a) for r, a in zip(rs, acts)]; v = [x for x in v if x]
    hits = [p for i, p, _ in v if p > i]
    return dict(n=len(v), pd=len(v)/ND[w], shown=100*len(hits)/len(v), roi=100*sum(p for _, p, _ in v)/sum(i for i, _, _ in v),
                med=statistics.median(hits) if hits else 0, b4=sum(p >= 40000 for p in hits)/ND[w], b2=sum(p >= 20000 for p in hits)/ND[w])
def boot(rs, acts, key, B=1000):
    days = defaultdict(list)
    for j, r in enumerate(rs): days[r["day"]].append(j)
    ks = list(days); rng = np.random.default_rng(0); d = []
    for _ in range(B):
        idx = [j for k in rng.integers(0, len(ks), len(ks)) for j in days[ks[k]]]
        a = met([rs[j] for j in idx], [acts[j] for j in idx], "確認"); b = met([rs[j] for j in idx], ["cur"]*len(idx), "確認")
        d.append(a[key] - b[key])
    return np.percentile(d, 2.5), np.percentile(d, 97.5)
ex = [r for r in R if r["win"] == "探索"]
for w in ("探索", "確認"):
    rs = [r for r in R if r["win"] == w]
    av = {a: np.mean([r["arms"][a] is not None for r in rs]) for a in rs[0]["arms"]}
    print(f"\n==== {w} A_hit n={len(rs)}  代替腕の組成率 " + " ".join(f"{a}:{100*v:.0f}%" for a, v in av.items()))
    # 安い決着の予測（pc5）の分離力
    q = np.quantile([r["f"]["pc5"] for r in ex], [.5, .8, .9])
    for lo, hi, nm in ((0, q[0], "pc5 下半分"), (q[0], q[1], "pc5 50-80%"), (q[1], q[2], "pc5 80-90%"), (q[2], 9, "pc5 上位10%")):
        s = [r for r in rs if lo <= r["f"]["pc5"] < hi]
        m = met(s, ["cur"]*len(s), w)
        cheap = 100*np.mean([np.isfinite(r["win_po"]) and r["win_po"] < 5 for r in s])
        t30 = met(s, ["T30k"]*len(s), w); t20 = met(s, ["T20k"]*len(s), w)
        print(f"  {nm:<12} n={len(s):>5} 決着5倍未満 {cheap:4.0f}% | 現行 的中{m['shown']:5.1f} ROI{m['roi']:6.1f} | 3万 的中{t30['shown']:5.1f} ROI{t30['roi']:6.1f} | 2万 的中{t20['shown']:5.1f} ROI{t20['roi']:6.1f}")
    print(f"  {'方針':<26}{'件/日':>6}{'表示的中':>8}{'ROI':>7}{'払戻中央':>9}{'2万+/日':>8}{'4万+/日':>8}   Δ的中CI / ΔROI CI  救済/破壊")
    base = met(rs, ["cur"]*len(rs), w)
    print(f"  {'現行(5万)':<26}{base['pd']:>6.2f}{base['shown']:>8.2f}{base['roi']:>7.1f}{base['med']:>9,.0f}{base['b2']:>8.2f}{base['b4']:>8.3f}")
    for qq in (0.7, 0.8, 0.9):
        thr = np.quantile([r["f"]["pc5"] for r in ex], qq)
        flag = [r["f"]["pc5"] >= thr for r in rs]
        fl_ex = [r for r in ex if r["f"]["pc5"] >= thr]
        cthr = np.median([r["f"]["top_cheap_p"] for r in fl_ex])
        pols = {f"見送り pc5上位{100-int(qq*100)}%": ["skip" if f else "cur" for f in flag]}
        for T in ("T30k", "T25k", "T20k"):
            pols[f"{T} pc5上位{100-int(qq*100)}%"] = [T if f else "cur" for f in flag]
            pols[f"混合{T} 上位{100-int(qq*100)}%"] = [(T if r["f"]["top_cheap_p"] >= cthr else "skip") if f else "cur" for r, f in zip(rs, flag)]
        for nm, acts in pols.items():
            m = met(rs, acts, w)
            ci = boot(rs, acts, "shown"); cr = boot(rs, acts, "roi")
            resc = sum(1 for r, a in zip(rs, acts) if a not in ("cur", "skip") and res(r, a)[1] > res(r, a)[0] and not r["pay"] > r["inv"])
            brk = sum(1 for r, a in zip(rs, acts) if a != "cur" and r["pay"] > r["inv"] and not (a != "skip" and res(r, a)[1] > res(r, a)[0]))
            print(f"  {nm:<26}{m['pd']:>6.2f}{m['shown']:>8.2f}{m['roi']:>7.1f}{m['med']:>9,.0f}{m['b2']:>8.2f}{m['b4']:>8.3f}   [{ci[0]:+.1f},{ci[1]:+.1f}] / [{cr[0]:+.1f},{cr[1]:+.1f}]  {resc}/{brk}")
    # 無作為に同数を見送る対照（上位20%見送りと同数）
    thr = np.quantile([r["f"]["pc5"] for r in ex], 0.8); k = sum(r["f"]["pc5"] >= thr for r in rs)
    rng = np.random.default_rng(1); ctl = []
    for s in range(20):
        drop = set(rng.choice(len(rs), k, replace=False))
        ctl.append(met(rs, ["skip" if j in drop else "cur" for j in range(len(rs))], w))
    print(f"  無作為見送り対照（{k}件・20本）: 的中 中央{np.median([c['shown'] for c in ctl]):.2f}  ROI 中央{np.median([c['roi'] for c in ctl]):.1f}  ROI 範囲[{min(c['roi'] for c in ctl):.1f},{max(c['roi'] for c in ctl):.1f}]")
